import os
import yaml
import time
import re
import base64
from datetime import datetime
import urllib.request
import urllib.error
import urllib.parse
import json
import logging

import feedparser
from newspaper import Article, Config, ArticleException
from github import Github
import ebooklib
from ebooklib import epub
import warnings

# Suppress harmless ebooklib warnings mapping to futures
warnings.filterwarnings('ignore', category=UserWarning, module='ebooklib.epub')
warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib.epub')
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DataFetcher:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.config_path = os.path.join(base_dir, 'config', 'sources.yml')
        self.source_dir = os.path.join(base_dir, 'source')
        self.article_list_dir = os.path.join(base_dir, 'articleList')
        self.github_token = os.environ.get('GITHUB_TOKEN', None) # Setup in GH Actions
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def run(self):
        updated_sources = []
        for source in self.config.get('sources', []):
            try:
                if source['source_type'] == 'rss':
                    updated_source = self.fetch_rss(source)
                elif source['source_type'] == 'github':
                    updated_source = self.fetch_github(source)
                else:
                    logging.warning(f"Unknown source type: {source['source_type']}")
                    updated_source = source
                updated_sources.append(updated_source)
            except Exception as e:
                logging.error(f"Error fetching source {source['journal_name']}: {e}")
                updated_sources.append(source)

        # Update sources.yml with new last_fetched_date
        self.config['sources'] = updated_sources
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.config, f, allow_unicode=True)

    def fetch_rss(self, source):
        logging.info(f"Fetching RSS: {source['journal_name']}")
        feed = feedparser.parse(source['url'])
        today_str = datetime.now().strftime("%m.%d.%Y")
        
        # If already fetched today, skip (optional, based on requirement)
        if source.get('last_fetched_date') == today_str:
            logging.info(f"{source['journal_name']} already fetched today.")
            return source

        articles_data = []
        raw_source_content = {}
        
        # Setup modern User-Agent to bypass Cloudflare/403 blocks on sites like Science.org
        np_config = Config()
        np_config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        np_config.headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Upgrade-Insecure-Requests': '1'
        }
        np_config.request_timeout = 15
        
        for idx, entry in enumerate(feed.entries):
            title = entry.get('title', 'Unknown Title')
            link = entry.get('link', '')
            if not link:
                continue
                
            try:
                article = Article(link, config=np_config)
                article.download()
                article.parse()
                text = article.text
            except Exception as e:
                logging.warning(f"Failed to extract {link}: {e}")
                continue
                
            if len(text) < 1000:
                logging.info(f"Skipping short article: {title}")
                continue
            
            article_id = f"rss_{idx}_{int(time.time())}"
            articles_data.append({
                'id': article_id,
                'title': title,
                'is_used': False
            })
            
            raw_source_content[article_id] = {
                'title': title,
                'text': text
            }
            
            if len(articles_data) >= 10:
                break
                
        if articles_data:
            self.save_raw_source(source, today_str, "json", json.dumps(raw_source_content, ensure_ascii=False, indent=2))
            self.save_article_list(source, today_str, articles_data)
            source['last_fetched_date'] = today_str
            
        return source

    def fetch_github(self, source):
        logging.info(f"Fetching GitHub: {source['url']} for {source['journal_name']}")
        if not self.github_token:
            logging.warning("No GITHUB_TOKEN provided, may hit rate limits.")
        
        g = Github(self.github_token)
        repo_name = source['url']
        try:
            repo = g.get_repo(repo_name)
        except Exception as e:
            logging.error(f"Cannot access repo {repo_name}: {e}")
            return source

        today_str = datetime.now().strftime("%m.%d.%Y")
        current_year = datetime.now().strftime("%Y")
        
        # Determine the possible directory based on journal_name
        journal_dir_map = {
            "Economist": "01_economist",
            "New Yorker": "02_new_yorker",
            "Scientific American": "03_scientific_american",
            "Atlantic": "04_atlantic",
            "Wired": "05_wired"
        }
        target_path = journal_dir_map.get(source['journal_name'])
        if not target_path:
            logging.error(f"Unknown GitHub source mapping for {source['journal_name']}")
            return source
        
        try:
            contents = repo.get_contents(target_path)
            # Find all subdirectories
            subdirs = [c for c in contents if c.type == "dir"]
            if not subdirs:
                logging.info(f"No subdirectories found in {target_path}")
                return source
                
            # Sort by name assuming the name contains a sortable date
            subdirs.sort(key=lambda x: x.name, reverse=True)
            latest_dir = subdirs[0]
            
            # Fetch contents of the latest directory
            dir_contents = repo.get_contents(latest_dir.path)
            epub_files = [c for c in dir_contents if c.name.endswith('.epub')]
            if not epub_files:
                logging.info(f"No EPUB files found in {latest_dir.path}")
                return source
                
            epub_files.sort(key=lambda x: x.name, reverse=True)
            latest_file = epub_files[0]
            
            # Extract date using regex or just use the filename
            match = re.search(r'(\d{4}-\d{2}-\d{2})', latest_file.name)
            if match:
                date_str = match.group(1)
                # Convert YYYY-MM-DD to MM.DD.YYYY
                publish_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m.%d.%Y")
            else:
                publish_date = today_str

            if self.is_raw_source_fetched(source, publish_date, "json"):
                logging.info(f"{source['journal_name']} EPUB already parsed and up-to-date ({publish_date}).")
                return source

            # Download EPUB temporarily via direct download_url to avoid 'unsupported encoding: none' for large files
            download_url = latest_file.download_url
            if not download_url:
                logging.error(f"No download URL for {latest_file.name}")
                return source

            req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0 (meowReader)'})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    epub_content = response.read()
            except Exception as e:
                logging.error(f"Failed to download EPUB via stream: {e}")
                return source
                
            self.save_raw_source(source, publish_date, "epub", epub_content, mode='wb')
            epub_path = self.get_raw_source_path(source, publish_date, "epub")
            
            # Parse EPUB chapters with EbookLib and extract text
            articles_data, extracted_texts = self.parse_epub(epub_path)
            
            # Delete EPUB to prevent Git bloat
            if os.path.exists(epub_path):
                os.remove(epub_path)
                logging.info(f"Deleted EPUB file to save space: {epub_path}")
            
            if articles_data:
                # Save purely extracted JSON text
                self.save_raw_source(source, publish_date, "json", json.dumps(extracted_texts, ensure_ascii=False, indent=2))
                self.save_article_list(source, publish_date, articles_data)
                source['last_fetched_date'] = publish_date

        except Exception as e:
            logging.error(f"Error checking GitHub contents for {source['journal_name']}: {e}")
            
        return source

    def get_raw_source_path(self, source, date_str, ext):
        freq_dir = os.path.join(self.source_dir, source['update_frequency'], source['journal_name'].replace(' ', '_'))
        os.makedirs(freq_dir, exist_ok=True)
        return os.path.join(freq_dir, f"{date_str}.{ext}")

    def save_raw_source(self, source, date_str, ext, content, mode='w'):
        file_path = self.get_raw_source_path(source, date_str, ext)
        # If binary
        if mode == 'wb':
            with open(file_path, mode) as f:
                f.write(content)
        else:
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)
        logging.info(f"Saved raw source to {file_path}")

    def parse_epub(self, epub_path):
        """Parse EPUB and extract chapters > 1000 characters as articles."""
        articles = []
        extracted_texts = {}
        book = epub.read_epub(epub_path)
        
        for idx, item in enumerate(book.get_items()):
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                text = soup.get_text(separator='\n', strip=True)
                title_val = soup.title.string if soup.title else None
                title = str(title_val).strip() if title_val else f"Chapter {idx}"
                
                if len(text) > 1000:
                    art_id = f"epub_{idx}"
                    articles.append({
                        'id': art_id,
                        'title': title[:100],
                        'is_used': False
                    })
                    extracted_texts[art_id] = {
                        'title': title,
                        'text': text
                    }
                    
        return articles, extracted_texts

    def save_article_list(self, source, date_str, articles):
        freq_dir = os.path.join(self.article_list_dir, source['update_frequency'], source['journal_name'].replace(' ', '_'))
        os.makedirs(freq_dir, exist_ok=True)
        file_path = os.path.join(freq_dir, f"{source['journal_name'].replace(' ', '_')}-{date_str}.yml")
        
        data = {
            'journal_name': source['journal_name'],
            'publish_date': date_str,
            'capture_date': datetime.now().strftime("%m.%d.%Y"),
            'source_type': source['source_type'],
            'update_frequency': source['update_frequency'],
            'articles': articles
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True)
        logging.info(f"Saved article list to {file_path}")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fetcher = DataFetcher(base_dir)
    fetcher.run()

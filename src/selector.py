import os
import yaml
import glob
import random
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ArticleSelector:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.article_list_dir = os.path.join(base_dir, 'articleList')
        self.reading_dir = os.path.join(base_dir, 'reading')

    def get_unused_articles(self, frequency):
        """Returns a list of tuples (file_path, journal_name, article_dict) for universally unused articles"""
        unused = []
        freq_path = os.path.join(self.article_list_dir, frequency, '*')
        journal_dirs = glob.glob(freq_path)
        
        for p in journal_dirs:
            if not os.path.isdir(p):
                continue
            yaml_files = glob.glob(os.path.join(p, '*.yml'))
            for y_file in yaml_files:
                with open(y_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    for idx, art in enumerate(data.get('articles', [])):
                        if not art.get('is_used', False):
                            unused.append({
                                'file_path': y_file,
                                'journal_name': data.get('journal_name'),
                                'publish_date': data.get('publish_date'),
                                'article': art,
                                'article_idx': idx
                            })
        return unused

    def mark_as_used(self, selected_articles):
        # selected_articles is a list of the dicts returned from get_unused_articles
        # To avoid multiple file opens, group by file_path
        updates_by_file = {}
        for item in selected_articles:
            if item['file_path'] not in updates_by_file:
                with open(item['file_path'], 'r', encoding='utf-8') as f:
                    updates_by_file[item['file_path']] = yaml.safe_load(f)
            
            # Find the article in the loaded data and mark used
            for art in updates_by_file[item['file_path']]['articles']:
                if art['id'] == item['article']['id']:
                    art['is_used'] = True
                    break

        # Save back to disk
        for file_path, data in updates_by_file.items():
            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(data, f, allow_unicode=True)

    def select_articles(self, strategy="common"):
        """
        common strategy: 4 articles total. 1 weekly, 1 monthly, 2 daily.
        manual strategy: 4 articles total. 4 daily.
        If weekly/monthly runs out, backfill with daily.
        """
        selected = []
        daily_pool = self.get_unused_articles("daily")
        weekly_pool = self.get_unused_articles("weekly")
        monthly_pool = self.get_unused_articles("monthly")
        
        daily_needed = 0
        if strategy == "common":
            # 1 from weekly
            if weekly_pool:
                choice = random.choice(weekly_pool)
                selected.append(choice)
                weekly_pool.remove(choice)
            else:
                daily_needed += 1
                
            # 1 from monthly
            if monthly_pool:
                choice = random.choice(monthly_pool)
                selected.append(choice)
                monthly_pool.remove(choice)
            else:
                daily_needed += 1
                
            daily_needed += 2
        else:
            daily_needed = 4
            
        # Backfill from daily
        if len(daily_pool) >= daily_needed:
            choices = random.sample(daily_pool, daily_needed)
            selected.extend(choices)
        else:
            logging.warning(f"Not enough daily articles. Found {len(daily_pool)} but need {daily_needed}.")
            selected.extend(daily_pool)
            
        return selected

    def generate_reading_list(self, strategy="common"):
        date_str = datetime.now().strftime("%m.%d.%Y")
        selected = self.select_articles(strategy)
        
        if not selected:
            logging.error("No articles found to generate reading list.")
            return

        self.mark_as_used(selected)
        
        # Save to reading list
        out_dir = os.path.join(self.reading_dir, date_str)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"{strategy}.yml")
        
        output_data = {
            'generate_date': date_str,
            'strategy': strategy,
            'articles': []
        }
        
        for item in selected:
            output_data['articles'].append({
                'journal_name': item['journal_name'],
                'publish_date': item['publish_date'],
                'article_id': item['article']['id'],
                'title': item['article']['title']
            })
            
        with open(out_file, 'w', encoding='utf-8') as f:
            yaml.safe_dump(output_data, f, allow_unicode=True)
            
        logging.info(f"Generated {out_file} with {len(selected)} articles.")
        return out_file

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    selector = ArticleSelector(base_dir)
    # Use environment var or default to common
    strategy = os.environ.get('STRATEGY', 'common')
    selector.generate_reading_list(strategy)

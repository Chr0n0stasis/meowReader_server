import os
import yaml
import json
import logging
from google import genai
from google.genai import types
from datetime import datetime
import typing_extensions as typing

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class QuestionOption(typing.TypedDict):
    A: str
    B: str
    C: str
    D: str

class MockQuestion(typing.TypedDict):
    q_number: int
    stem: str
    options: QuestionOption
    answer: str
    explanation: str

class MockPaperOutput(typing.TypedDict):
    difficulty_constant: float
    questions: list[MockQuestion]

class MockPaperGenerator:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.reading_dir = os.path.join(base_dir, 'reading')
        self.source_dir = os.path.join(base_dir, 'source')
        self.artifacts_dir = os.path.join(base_dir, 'artifacts')
        self.api_key = os.environ.get('GEMINI_API_KEY')
        if not self.api_key:
            logging.error("GEMINI_API_KEY environment variable not set. Gemini generation will fail.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)
            
        self.model_names = ['gemini-3.1-flash-lite', 'gemini-3.0-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-flash'] if self.api_key else ['dummy']

    def run(self, strategy="common"):
        date_str = datetime.now().strftime("%m.%d.%Y")
        reading_file = os.path.join(self.reading_dir, date_str, f"{strategy}.yml")
        
        if not os.path.exists(reading_file):
            logging.error(f"Reading list {reading_file} not found.")
            return

        with open(reading_file, 'r', encoding='utf-8') as f:
            reading_data = yaml.safe_load(f)

        articles = reading_data.get('articles', [])
        if not articles:
            logging.error("No articles in reading list.")
            return

        # Sort articles
        priority = {
            'Nature': 10, 'Science': 10, 'Economist': 9, 'New Yorker': 8,
            'Scientific American': 7, 'The Guardian': 6, 'China Daily': 5
        }
        articles.sort(key=lambda x: priority.get(x['journal_name'], 0), reverse=True)

        papers = []
        for idx, item in enumerate(articles):
            question_type = "Use of English" if idx == 0 else "Reading Comprehension"
            body_text = self._extract_text(item)
            
            if not body_text:
                logging.warning(f"Could not extract text for {item['title']}. Skipping.")
                continue

            # Limit text length to roughly 2000 words to fit context and prompt well
            body_text = " ".join(body_text.split()[:2000])
            
            logging.info(f"Generating questions for: {item['title']} as {question_type}")
            llm_result = self._call_gemini(body_text, question_type)
            
            if not llm_result:
                logging.error(f"Failed to generate questions for {item['title']}")
                continue
                
            papers.append({
                'title': item['title'],
                'article_update_date': item['publish_date'],
                'source_journal': item['journal_name'],
                'body_text': body_text,
                'difficulty_constant': llm_result.get('difficulty_constant', 5.0),
                'question_type': question_type,
                'questions': llm_result.get('questions', [])
            })

        output_data = {
            'group_update_date': date_str,
            'papers': papers
        }

        if not papers:
            logging.error("Failed to generate any questions. Exiting to prevent publishing empty paper.")
            import sys
            sys.exit(1)

        self._save_artifacts(date_str, strategy, output_data)

    def _extract_text(self, item):
        journal = item['journal_name'].replace(' ', '_')
        pub_date = item['publish_date']
        art_id = item['article_id']
        
        possible_paths = []
        for freq in ['daily', 'weekly', 'monthly']:
            base_p = os.path.join(self.source_dir, freq, journal)
            possible_paths.append(os.path.join(base_p, f"{pub_date}.json"))
            
        source_file = next((p for p in possible_paths if os.path.exists(p)), None)
        
        if not source_file:
            return None
            
        try:
            with open(source_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if art_id in data:
                    return data[art_id].get('text', '')
        except Exception as e:
            logging.error(f"Error reading JSON {source_file}: {e}")
                
        return None

    def _call_gemini(self, text, q_type, retries=3):
        if not self.client:
            return {
                "difficulty_constant": 8.5,
                "questions": [
                    {
                        "q_number": 1,
                        "stem": "Sample question generated without API key?",
                        "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                        "answer": "A",
                        "explanation": "Sample explanation"
                    }
                ]
            }

        prompt = f"""
你是一位高级考研英语一命题组专家。请根据以下文章，先计算其 difficulty_constant (难度定数，基于长难句和超纲词汇密度，1.0到15.0的浮点数，对标 maimai Rating 系统)。
然后为其生成 5 道符合考研大纲要求的高质量单选题及中文解析。
题型：{q_type}。文章内容：
{text}
"""
        import time
        for model_variant in self.model_names:
            for attempt in range(retries):
                try:
                    response = self.client.models.generate_content(
                        model=model_variant,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=MockPaperOutput
                        )
                    )
                    logging.info(f"Successfully generated with model {model_variant}")
                    return json.loads(response.text)
                except Exception as e:
                    logging.error(f"Gemini API error with {model_variant} (attempt {attempt+1}/{retries}): {e}")
                    # Only sleep if it's a transient error, but if not found we break straight to next model
                    if "is not found" in str(e) or "404" in str(e):
                        break
                    time.sleep(2)
        return None

    def _save_artifacts(self, date_str, strategy, data):
        out_dir = os.path.join(self.artifacts_dir, date_str)
        os.makedirs(out_dir, exist_ok=True)
        
        yml_path = os.path.join(out_dir, f"{strategy}.yml")
        json_path = os.path.join(out_dir, f"{strategy}.json")
        
        with open(yml_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True)
            
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        logging.info(f"Saved mock paper to {yml_path} and {json_path}")
        
        # Update global index.json for Client-Server Sync
        self._update_global_index(date_str, strategy)

    def _update_global_index(self, date_str, strategy):
        index_path = os.path.join(self.artifacts_dir, 'index.json')
        index_data = []
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
            except Exception as e:
                logging.error(f"Error reading index.json: {e}")
                
        # Avoid duplicates
        paper_id = f"{date_str}-{strategy}"
        for item in index_data:
            if item.get('id') == paper_id:
                return # Already exists
                
        index_data.append({
            "id": paper_id,
            "date": date_str,
            "strategy": strategy,
            "path": f"artifacts/{date_str}/{strategy}.json"
        })
        
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
            
        logging.info(f"Updated global index.json with new paper {paper_id}")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    generator = MockPaperGenerator(base_dir)
    strategy = os.environ.get('STRATEGY', 'common')
    generator.run(strategy)

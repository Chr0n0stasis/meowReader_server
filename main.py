import os
import sys
import logging
from src.fetcher import DataFetcher
from src.selector import ArticleSelector
from src.generator import MockPaperGenerator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    strategy = os.environ.get('STRATEGY', 'common')
    
    logging.info("=== Starting Data Fetcher ===")
    fetcher = DataFetcher(base_dir)
    fetcher.run()
    
    logging.info("=== Starting Article Selector ===")
    selector = ArticleSelector(base_dir)
    selector.generate_reading_list(strategy)
    
    logging.info("=== Starting AI Generator ===")
    generator = MockPaperGenerator(base_dir)
    generator.run(strategy)
    
    logging.info("=== Pipeline completed successfully ===")

if __name__ == "__main__":
    main()

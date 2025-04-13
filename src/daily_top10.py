import arxiv
import datetime
from typing import List, Dict
import pandas as pd
import pytz
import time
import os
import schedule
from rank_papers import PaperQualityAnalyzer
from paper_analyzer import PaperAnalyzer
from analysis_manager import AnalysisManager
import json
from src.services.email_sender import EmailSender

# Initialize analyzers
paper_analyzer = PaperAnalyzer()
analysis_manager = AnalysisManager()

def get_specific_date_papers(target_date: str) -> List[Dict]:
    # UTC 기준으로 특정 날짜 계산
    target_start = datetime.datetime.strptime(target_date, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
    target_end = target_start + datetime.timedelta(days=1)
    
    # arXiv 검색 쿼리 생성
    client = arxiv.Client()
    search = arxiv.Search(
        query='cat:cs.AI',
        max_results=200,  # 더 많은 결과를 가져옴
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )
    
    # 논문 수집
    papers = []
    error_retries = 0
    try:
        for paper in client.results(search):
            # target_end보다 이후의 논문은 건너뛰기
            if paper.published >= target_end:
                continue
            # target_start보다 이전의 논문은 더 이상 볼 필요 없음
            if paper.published < target_start:
                # 하지만 논문이 하나도 없으면 더 이전 날짜까지 확인
                if len(papers) == 0 and error_retries < 3:
                    error_retries += 1
                    print(f"해당 날짜({target_date})의 논문을 찾지 못했습니다. 이전 날짜 확인 중... ({error_retries}/3)")
                    # 검색 범위를 1일 더 확장
                    target_start = target_start - datetime.timedelta(days=1)
                    continue
                break
            # target_start와 target_end 사이의 논문만 수집
            papers.append(paper)
            time.sleep(0.1)
    except Exception as e:
        print(f"논문 수집 중 오류 발생: {e}")
        if not papers:
            raise
    
    return papers

def save_top10(papers: List[Dict], analyzer: PaperQualityAnalyzer, target_date: str):
    # 논문 품질 점수 계산 및 정렬
    paper_scores = []
    for paper in papers:
        score = analyzer.analyze_paper(paper)
        paper_scores.append({
            'rank': 0,
            'title': paper.title.replace('\n', ' '),
            'url': paper.entry_id,
            'score': score,
            'authors': len(paper.authors),
            'categories': ', '.join(paper.categories),
            'published': paper.published.strftime('%Y-%m-%d'),
            'updated': paper.updated.strftime('%Y-%m-%d'),
            'abstract': paper.summary.replace('\n', ' ')
        })
    
    # 점수로 정렬
    paper_scores.sort(key=lambda x: x['score'], reverse=True)
    
    # 상위 10개만 선택
    top10 = paper_scores[:10]
    
    # 순위 추가
    for i, paper in enumerate(top10, 1):
        paper['rank'] = i
    
    # DataFrame 생성
    df = pd.DataFrame(top10)
    
    # 결과 출력
    print(f"\n=== {target_date}의 Top 10 논문 ===")
    print("순위 | 제목 | URL | 품질점수 | 저자수 | 카테고리 | 게시일 | 수정일")
    print("-" * 150)
    
    for _, paper in df.iterrows():
        title = paper['title'][:70] + '...' if len(paper['title']) > 70 else paper['title']
        print(f"{paper['rank']:2d} | {title} | {paper['url']} | {paper['score']:.2f} | {paper['authors']} | {paper['categories']} | {paper['published']} | {paper['updated']}")
    
    # CSV 파일로 저장
    os.makedirs('data/daily_top10', exist_ok=True)
    current_date = datetime.datetime.now(pytz.UTC).strftime('%Y%m%d')
    csv_file = f'data/daily_top10/top10_{current_date}.csv'
    df.to_csv(csv_file, index=False, encoding='utf-8')
    print(f"\nTop 10이 CSV 파일로 저장되었습니다: {csv_file}")
    
    return top10

def analyze_and_generate_report(papers: List[Dict], target_date: str):
    """논문을 분석하고 보고서를 생성합니다."""
    print("논문 분석 중...")
    
    # Create necessary directories
    os.makedirs("data/analysis", exist_ok=True)
    os.makedirs("config", exist_ok=True)
    
    # Initialize analyzers
    analyzer = PaperQualityAnalyzer()
    
    # Get top 10 papers
    top10_papers = save_top10(papers, analyzer, target_date)
    
    # Analyze papers
    analysis_results = []
    for paper in top10_papers:
        print(f"\n논문 분석 시작: {paper['title']}")
        result = paper_analyzer.analyze_paper(paper)
        # Add submission_date and html_url to result
        result['submission_date'] = paper['published']
        result['html_url'] = paper['url']
        analysis_results.append(result)
        print(f"분석 완료: {paper['title']}")
    
    # Save analysis results
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    analysis_file = f"data/analysis/analysis_results_{timestamp}.json"
    
    with open(analysis_file, "w", encoding="utf-8") as f:
        json.dump(analysis_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n분석 결과가 저장되었습니다: {analysis_file}")
    
    # Generate HTML report
    print("\nHTML 보고서 생성 중...")
    report_file = analysis_manager.generate_report(analysis_results)
    print(f"HTML 보고서가 생성되었습니다: {report_file}")
    
    # Send email report
    print("\n이메일 발송 중...")
    email_sender = EmailSender()
    email_sender.send_report(analysis_results)
    
    return analysis_results

def run_daily_top10():
    try:
        # UTC 기준으로 전날 날짜 계산
        utc = pytz.UTC
        today = datetime.datetime.now(utc)
        yesterday = today - datetime.timedelta(days=1)
        target_date = yesterday.strftime('%Y-%m-%d')
        
        print(f"{target_date}의 cs.AI 논문을 가져오는 중...")
        papers = get_specific_date_papers(target_date)
        print(f"총 {len(papers)}개의 논문을 가져왔습니다.")
        
        # 분석 및 보고서 생성
        analyze_and_generate_report(papers, target_date)
    except Exception as e:
        print(f"오류 발생: {e}")

def main():
    # 테스트 실행
    run_daily_top10()

if __name__ == "__main__":
    main() 
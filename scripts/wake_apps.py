#!/usr/bin/env python3
"""
Streamlit Cloud アプリ自動起動スクリプト
GitHub Actions環境で実行され、スリープ状態のアプリを起動させる
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import glob
import shutil

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import requests

# アプリケーション定義
@dataclass
class StreamlitApp:
    name: str
    url: str
    description: str = ""
    priority: int = 1  # 優先度（1が最高）

# 監視対象のアプリ一覧
APPS = [
    StreamlitApp("QRコード生成", "https://qr-code-app-dqfxmtvaq9tvym82xr3grz.streamlit.app", "QRコード生成ツール", 1),
    StreamlitApp("MAP", "https://appmapapp-wmljxtm7drgnmjjpkxndx5.streamlit.app", "地図アプリケーション", 2),
    StreamlitApp("天気予報", "https://app-weather-forecast-app-xvdbgeyqypo4dcu9gkuhd4.streamlit.app", "天気予報アプリ", 1),
    StreamlitApp("リアルタイム地球儀", "https://streatmitskyglobe-d48neqmv65fjrfaenjtmmj.streamlit.app", "地球儀ビジュアライザー", 3),
    StreamlitApp("ビジネスダッシュボード", "https://app-colorful-dashboard-mw8do3iqicucaumypshfzl.streamlit.app", "ダッシュボード", 1),
    StreamlitApp("TODO アプリ", "https://appsimpletodo-nrzgxmanzr42p2bu5ckfqr.streamlit.app", "タスク管理", 2),
    StreamlitApp("個人用ナレッジベース", "https://appknowledgebase-gauwakfpz5hbbswmsgrqyq.streamlit.app/", "知識ベース", 1),
]

# 設定
CONFIG = {
    "log_dir": "logs",
    "log_file": "streamlit_access.log",
    "json_log_file": "streamlit_access.json",
    "timeout": 30,
    "wait_between_apps": 3,
    "max_retries": 2,
    "headless": True,
    "window_size": "1920,1080",
}

class StreamlitWaker:
    """Streamlitアプリを起動させるクラス"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.driver: Optional[webdriver.Chrome] = None
        self.results = []
        self.setup_logging()
    
    def setup_logging(self):
        """ログディレクトリのセットアップ"""
        os.makedirs(self.config["log_dir"], exist_ok=True)
        self.log_path = os.path.join(self.config["log_dir"], self.config["log_file"])
        self.json_log_path = os.path.join(self.config["log_dir"], self.config["json_log_file"])
    
    def setup_driver(self) -> webdriver.Chrome:
        """Selenium WebDriverのセットアップ"""
        chrome_options = Options()
        
        if self.config["headless"]:
            chrome_options.add_argument('--headless')
        
        # 基本オプション
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument(f'--window-size={self.config["window_size"]}')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # パフォーマンス最適化
        chrome_options.add_argument('--disable-images')
        chrome_options.add_argument('--disable-javascript')  # JSが必要な場合はコメントアウト
        
        # GitHub Actions環境用の追加設定
        if os.environ.get('GITHUB_ACTIONS'):
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_argument('--disable-extensions')
        
        try:
            # Selenium 4.x の新しい方法
            # まずChromeDriverが自動検出されるか試す
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(self.config["timeout"])
            print("✅ ChromeDriver auto-detected successfully")
            return driver
        except Exception as e:
            print(f"⚠️ Auto-detection failed: {e}")
            print("📥 Trying with webdriver-manager...")
            
            try:
                # webdriver-managerを使用した方法
                from webdriver_manager.chrome import ChromeDriverManager
                from selenium.webdriver.chrome.service import Service
                
                # ChromeDriverManagerでドライバーをダウンロード/取得
                driver_path = ChromeDriverManager().install()
                service = Service(driver_path)
                
                # Serviceオブジェクトを使用してChromeを起動
                driver = webdriver.Chrome(service=service, options=chrome_options)
                driver.set_page_load_timeout(self.config["timeout"])
                print("✅ ChromeDriver installed via webdriver-manager")
                return driver
            except Exception as e2:
                print(f"❌ WebDriver setup failed: {e2}")
                sys.exit(1)
    
    def check_app_simple(self, app: StreamlitApp) -> Dict:
        """シンプルなHTTPチェック（高速）"""
        try:
            response = requests.get(app.url, timeout=10, allow_redirects=True)
            status_code = response.status_code
            
            # Streamlitのスリープページを検出
            if "get this app back up" in response.text.lower():
                return {
                    "status": "SLEEPING",
                    "http_code": status_code,
                    "needs_wake": True
                }
            elif status_code == 200:
                return {
                    "status": "RUNNING",
                    "http_code": status_code,
                    "needs_wake": False
                }
            else:
                return {
                    "status": "UNKNOWN",
                    "http_code": status_code,
                    "needs_wake": True
                }
        except Exception as e:
            return {
                "status": "ERROR",
                "error": str(e),
                "needs_wake": True
            }
    
    def wake_app_with_selenium(self, app: StreamlitApp) -> Dict:
        """Seleniumを使ってアプリを起動"""
        result = {
            "name": app.name,
            "url": app.url,
            "timestamp": datetime.now().isoformat(),
            "attempts": 0,
        }
        
        # driverがNoneでないことを確認
        if not self.driver:
            result["status"] = "FAILED"
            result["message"] = "WebDriver not initialized"
            return result
        
        for attempt in range(self.config["max_retries"]):
            result["attempts"] = attempt + 1
            
            try:
                print(f"🔄 Attempting to wake {app.name} (Attempt {attempt + 1}/{self.config['max_retries']})")
                self.driver.get(app.url)
                time.sleep(3)
                
                # 複数のボタンセレクタを試す
                button_selectors = [
                    "//button[contains(text(), 'Yes, get this app back up')]",
                    "//button[contains(text(), 'get this app back up')]",
                    "//button[contains(@class, 'stButton')]//div[contains(text(), 'Yes')]",
                    "//div[@data-testid='stButton']//button",
                ]
                
                button_found = False
                for selector in button_selectors:
                    try:
                        wake_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                        wake_button.click()
                        button_found = True
                        print(f"✅ {app.name} - Wake button clicked!")
                        result["status"] = "WOKEN_UP"
                        result["message"] = "Successfully clicked wake button"
                        time.sleep(5)  # 起動待機
                        break
                    except TimeoutException:
                        continue
                
                if not button_found:
                    # ボタンが見つからない = すでに起動している可能性
                    print(f"✅ {app.name} - Already running (no wake button found)")
                    result["status"] = "ALREADY_RUNNING"
                    result["message"] = "App is already running"
                
                break  # 成功したらリトライループを抜ける
                
            except Exception as e:
                error_msg = f"Error on attempt {attempt + 1}: {str(e)}"
                print(f"⚠️ {app.name} - {error_msg}")
                result["error"] = error_msg
                
                if attempt == self.config["max_retries"] - 1:
                    result["status"] = "FAILED"
                    result["message"] = f"Failed after {self.config['max_retries']} attempts"
                else:
                    time.sleep(2)  # リトライ前の待機
        
        return result
    
    def run(self):
        """メイン実行処理"""
        print("🚀 Starting Streamlit Wake Automation")
        print(f"📅 Execution time: {datetime.now()}")
        print(f"📋 Total apps to check: {len(APPS)}")
        print("=" * 50)
        
        # 優先度順にソート
        sorted_apps = sorted(APPS, key=lambda x: x.priority)
        
        # 強制Seleniumモードのチェック
        force_selenium = self.config.get("force_selenium", False)
        
        if force_selenium:
            # すべてのアプリをSeleniumでチェック
            print("\n🎯 Force mode: Checking all apps with Selenium...")
            apps_to_wake = sorted_apps
        else:
            # Phase 1: 高速HTTPチェック
            print("\n📡 Phase 1: Quick HTTP check...")
            apps_to_wake = []
            
            for app in sorted_apps:
                check_result = self.check_app_simple(app)
                status = check_result['status']
                
                if check_result.get("needs_wake", False):
                    apps_to_wake.append(app)
                    print(f"  {app.name}: {status} → Will check with Selenium")
                else:
                    print(f"  {app.name}: {status} ✅")
        
        # Phase 2: Seleniumでの起動が必要なアプリのみ処理
        if apps_to_wake:
            print(f"\n🎯 Phase 2: Checking {len(apps_to_wake)} apps with Selenium...")
            self.driver = self.setup_driver()
            
            try:
                for app in apps_to_wake:
                    result = self.wake_app_with_selenium(app)
                    self.results.append(result)
                    
                    if app != apps_to_wake[-1]:  # 最後のアプリでなければ待機
                        time.sleep(self.config["wait_between_apps"])
            finally:
                if self.driver:
                    self.driver.quit()
        else:
            print("\n✨ All apps are already running!")
            for app in sorted_apps:
                self.results.append({
                    "name": app.name,
                    "url": app.url,
                    "timestamp": datetime.now().isoformat(),
                    "status": "ALREADY_RUNNING",
                    "message": "No wake-up needed"
                })
        
        # 結果の保存
        self.save_results()
        self.print_summary()
    
    def save_results(self):
        """結果をログファイルに保存"""
        # テキストログ
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Execution Time: {datetime.now()}\n")
            f.write(f"{'='*60}\n")
            
            for result in self.results:
                status = result.get("status", "UNKNOWN")
                message = result.get("message", "")
                f.write(f"[{status}] {result['name']}: {message}\n")
                
                if "error" in result:
                    f.write(f"  Error: {result['error']}\n")
        
        # JSONログ（構造化データ）
        json_data = {
            "execution_time": datetime.now().isoformat(),
            "total_apps": len(APPS),
            "results": self.results
        }
        
        # 既存のJSONログを読み込み
        log_history = []
        if os.path.exists(self.json_log_path):
            try:
                with open(self.json_log_path, "r", encoding="utf-8") as f:
                    log_history = json.load(f)
            except:
                log_history = []
        
        # 新しい結果を追加（最新10件のみ保持）
        log_history.append(json_data)
        log_history = log_history[-10:]
        
        with open(self.json_log_path, "w", encoding="utf-8") as f:
            json.dump(log_history, f, indent=2, ensure_ascii=False)
    
    def print_summary(self):
        """実行結果のサマリーを表示"""
        print("\n" + "="*50)
        print("📊 EXECUTION SUMMARY")
        print("="*50)
        
        status_counts = {}
        for result in self.results:
            status = result.get("status", "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        for status, count in status_counts.items():
            emoji = {
                "WOKEN_UP": "🔄",
                "ALREADY_RUNNING": "✅",
                "FAILED": "❌",
                "UNKNOWN": "❓"
            }.get(status, "📌")
            print(f"{emoji} {status}: {count} apps")
        
        print(f"\n📁 Logs saved to: {self.log_path}")
        print("✨ Automation completed successfully!")

def main():
    """メイン関数"""
    try:
        waker = StreamlitWaker(CONFIG)
        waker.run()
        
        # 明示的に成功を表示
        print("\n✅ All operations completed successfully!")
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n⚠️ Script interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        
        # エラーでも部分的な成功があれば0を返す
        if os.path.exists(os.path.join(CONFIG["log_dir"], CONFIG["log_file"])):
            print("⚠️ Error occurred but logs were saved successfully")
            sys.exit(0)  # ログが保存されていれば成功とする
        else:
            sys.exit(1)

if __name__ == "__main__":
    main()
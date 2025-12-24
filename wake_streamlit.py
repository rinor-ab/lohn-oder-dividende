import os
import sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP_URL = os.environ.get("APP_URL")  # set in GitHub Actions secrets or env
if not APP_URL:
    print("Missing APP_URL env var")
    sys.exit(1)

def main():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(APP_URL)

        wait = WebDriverWait(driver, 20)

        # If app is asleep, Streamlit shows a wake-up button
        try:
            btn = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Yes, get this app back up')]")
                )
            )
            btn.click()
            print("Clicked wake-up button.")
        except Exception:
            print("Wake-up button not found. App likely already awake.")

        # Wait a bit for page to load or stabilize
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        print("Done.")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()

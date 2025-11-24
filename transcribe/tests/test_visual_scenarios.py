"""
Визуальные сценарные тесты с Selenium для наблюдения в браузере
"""
import pytest
import time
import os
import base64
from django.test import LiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from transcribe.models import Transcription

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


@pytest.mark.skipif(not SELENIUM_AVAILABLE, reason="Selenium not installed")
@pytest.mark.e2e
@pytest.mark.visual
class TestVisualScenarios(LiveServerTestCase):
    """Визуальные тесты с Selenium"""
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Настройка Chrome для видимого режима
        chrome_options = Options()
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        # НЕ используем headless - браузер должен быть видимым
        # chrome_options.add_argument('--headless')  # Закомментировано для видимости
        
        try:
            cls.driver = webdriver.Chrome(options=chrome_options)
        except Exception as e:
            pytest.skip(f"Chrome driver not available: {e}")
    
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'driver'):
            cls.driver.quit()
        super().tearDownClass()
    
    def take_screenshot(self, name):
        """Делает скриншот и возвращает base64"""
        screenshot_path = f"/tmp/screenshot_{name}.png"
        self.driver.save_screenshot(screenshot_path)
        with open(screenshot_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def test_visual_upload_scenario(self):
        """Визуальный тест: загрузка файла"""
        # Открываем главную страницу
        self.driver.get(self.live_server_url + '/')
        time.sleep(1)
        screenshot1 = self.take_screenshot('1_main_page')
        
        # Находим область загрузки
        upload_area = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "uploadArea"))
        )
        screenshot2 = self.take_screenshot('2_upload_area')
        
        # Кликаем по области загрузки
        upload_area.click()
        time.sleep(0.5)
        
        # Находим input файла
        file_input = self.driver.find_element(By.ID, "file")
        
        # Создаем тестовый файл
        test_file_path = "/tmp/test_visual.mp3"
        with open(test_file_path, 'wb') as f:
            f.write(b"fake audio content" * 100)
        
        # Загружаем файл
        file_input.send_keys(test_file_path)
        time.sleep(1)
        screenshot3 = self.take_screenshot('3_file_selected')
        
        # Заполняем подпись
        signature_input = self.driver.find_element(By.ID, "signature")
        signature_input.send_keys("Визуальный тест")
        time.sleep(0.5)
        screenshot4 = self.take_screenshot('4_signature_filled')
        
        # Выбираем модель
        model_select = self.driver.find_element(By.ID, "whisper_model")
        model_select.send_keys("base")
        time.sleep(0.5)
        
        # Нажимаем кнопку загрузки
        submit_btn = self.driver.find_element(By.ID, "submitBtn")
        submit_btn.click()
        time.sleep(2)
        screenshot5 = self.take_screenshot('5_uploading')
        
        # Ждем завершения загрузки
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CLASS_NAME, "transcription-item"))
        )
        time.sleep(2)
        screenshot6 = self.take_screenshot('6_uploaded')
        
        # Проверяем, что транскрипция появилась
        transcription_items = self.driver.find_elements(By.CLASS_NAME, "transcription-item")
        assert len(transcription_items) > 0
        
        # Очищаем тестовый файл
        if os.path.exists(test_file_path):
            os.remove(test_file_path)
    
    def test_visual_login_scenario(self):
        """Визуальный тест: вход по паролю"""
        # Открываем главную страницу
        self.driver.get(self.live_server_url + '/')
        time.sleep(1)
        
        # Прокручиваем к форме входа
        login_section = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "loginSection"))
        )
        self.driver.execute_script("arguments[0].scrollIntoView(true);", login_section)
        time.sleep(1)
        screenshot1 = self.take_screenshot('login_1_main')
        
        # Находим поле ввода пароля
        login_input = self.driver.find_element(By.ID, "login_phrase")
        login_input.send_keys("test_password_123")
        time.sleep(0.5)
        screenshot2 = self.take_screenshot('login_2_password_entered')
        
        # Нажимаем кнопку входа
        login_btn = self.driver.find_element(By.CSS_SELECTOR, ".btn-login")
        login_btn.click()
        time.sleep(2)
        screenshot3 = self.take_screenshot('login_3_logged_in')
        
        # Проверяем, что вход выполнен
        login_status = self.driver.find_element(By.CLASS_NAME, "login-status")
        assert "test_password_123" in login_status.text
    
    def test_visual_navigation_scenario(self):
        """Визуальный тест: навигация по интерфейсу"""
        # Создаем транскрипцию для теста
        transcription = Transcription.objects.create(
            filename="navigation_test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="completed",
            transcribed_text="Тестовая транскрипция для навигации"
        )
        
        # Открываем главную страницу
        self.driver.get(self.live_server_url + '/')
        time.sleep(2)
        screenshot1 = self.take_screenshot('nav_1_main')
        
        # Ищем транскрипцию
        transcription_items = self.driver.find_elements(By.CLASS_NAME, "transcription-item")
        if len(transcription_items) > 0:
            # Кликаем на ссылку "читать полностью"
            view_link = transcription_items[0].find_element(By.PARTIAL_LINK_TEXT, "читать полностью")
            view_link.click()
            time.sleep(2)
            screenshot2 = self.take_screenshot('nav_2_detail')
            
            # Проверяем, что мы на странице деталей
            assert "navigation_test.mp3" in self.driver.page_source
            
            # Возвращаемся назад
            back_link = self.driver.find_element(By.CLASS_NAME, "back-link")
            back_link.click()
            time.sleep(2)
            screenshot3 = self.take_screenshot('nav_3_back')
    
    def test_visual_file_selection_scenario(self):
        """Визуальный тест: выбор нескольких файлов"""
        self.driver.get(self.live_server_url + '/')
        time.sleep(1)
        
        # Кликаем по области загрузки
        upload_area = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "uploadArea"))
        )
        upload_area.click()
        time.sleep(0.5)
        
        # Создаем несколько тестовых файлов
        file_paths = []
        for i in range(3):
            file_path = f"/tmp/test_file_{i}.mp3"
            with open(file_path, 'wb') as f:
                f.write(b"content" * 100)
            file_paths.append(file_path)
        
        # Загружаем файлы
        file_input = self.driver.find_element(By.ID, "file")
        file_input.send_keys("\n".join(file_paths))
        time.sleep(2)
        screenshot = self.take_screenshot('multi_file_selected')
        
        # Проверяем, что файлы отображаются
        file_items = self.driver.find_elements(By.CLASS_NAME, "file-item")
        assert len(file_items) == 3
        
        # Очищаем тестовые файлы
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)


"""
Секретная страница для запуска сценарных тестов
"""
import json
import subprocess
import os
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt


def check_secret_key(request):
    """Проверка секретного ключа"""
    secret_key = request.GET.get('key') or request.POST.get('key', '')
    return secret_key == 'test-secret-2024'


@csrf_exempt
@require_http_methods(["GET", "POST"])
def secret_test_page(request):
    """Секретная страница для запуска сценарных тестов"""
    if not check_secret_key(request):
        return HttpResponse("Access Denied", status=403)
    
    if request.method == 'POST':
        # Запуск тестов
        action = request.POST.get('action', 'run')
        
        if action == 'run':
            try:
                # Запускаем pytest
                result = subprocess.run(
                    ['pytest', 'transcribe/tests/', '--verbose', '--tb=short', '-v'],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=settings.BASE_DIR
                )
                
                return JsonResponse({
                    'success': result.returncode == 0,
                    'returncode': result.returncode,
                    'stdout': result.stdout,
                    'stderr': result.stderr,
                    'message': 'Тесты завершены' if result.returncode == 0 else 'Тесты завершились с ошибками'
                })
            except subprocess.TimeoutExpired:
                return JsonResponse({
                    'success': False,
                    'error': 'Превышено время ожидания выполнения тестов'
                }, status=500)
            except Exception as e:
                return JsonResponse({
                    'success': False,
                    'error': str(e)
                }, status=500)
        elif action == 'status':
            # Проверка статуса тестов
            return JsonResponse({
                'status': 'ready',
                'message': 'Система тестирования готова'
            })
    
    # GET запрос - показываем страницу
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Секретная страница тестирования</title>
        <style>
            body {
                font-family: monospace;
                background: #1a1a1a;
                color: #0f0;
                padding: 20px;
                margin: 0;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            h1 {
                color: #0f0;
                border-bottom: 2px solid #0f0;
                padding-bottom: 10px;
            }
            .test-controls {
                background: #2a2a2a;
                padding: 20px;
                border: 2px solid #0f0;
                margin: 20px 0;
            }
            button {
                background: #0f0;
                color: #000;
                border: none;
                padding: 15px 30px;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
                margin: 10px 5px;
            }
            button:hover {
                background: #0a0;
            }
            button:disabled {
                background: #333;
                color: #666;
                cursor: not-allowed;
            }
            #output {
                background: #000;
                color: #0f0;
                padding: 20px;
                border: 2px solid #0f0;
                margin: 20px 0;
                min-height: 400px;
                max-height: 800px;
                overflow-y: auto;
                font-family: monospace;
                white-space: pre-wrap;
                word-wrap: break-word;
            }
            .status {
                padding: 10px;
                margin: 10px 0;
                border-left: 4px solid #0f0;
                background: #2a2a2a;
            }
            .error {
                color: #f00;
                border-left-color: #f00;
            }
            .success {
                color: #0f0;
                border-left-color: #0f0;
            }
            .loading {
                color: #ff0;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔒 Секретная страница тестирования</h1>
            
            <div class="test-controls">
                <h2>Управление тестами</h2>
                <button id="runTests" onclick="runTests()">▶ Запустить все тесты</button>
                <button id="runScenarios" onclick="runScenarios()">🎬 Запустить сценарные тесты</button>
                <button id="runIntegration" onclick="runIntegration()">🔗 Запустить интеграционные тесты</button>
                <button id="runE2E" onclick="runE2E()">🚀 Запустить E2E тесты</button>
                <button id="runVisual" onclick="runVisual()">👁️ Запустить визуальные тесты (Selenium)</button>
                <button id="checkStatus" onclick="checkStatus()">📊 Проверить статус</button>
                <button onclick="clearOutput()">🗑 Очистить вывод</button>
                <div style="margin-top: 20px; padding: 15px; background: #3a3a3a; border: 2px solid #0f0;">
                    <strong>⚠️ Визуальные тесты:</strong><br>
                    Для визуального наблюдения тестов убедитесь, что:<br>
                    1. Установлен Chrome и chromedriver<br>
                    2. Запущен X server (для Linux: Xvfb :99 &)<br>
                    3. Браузер будет открыт и виден во время тестов
                </div>
            </div>
            
            <div id="status" class="status">Готов к запуску тестов</div>
            
            <div id="output"></div>
        </div>
        
        <script>
            const output = document.getElementById('output');
            const status = document.getElementById('status');
            const secretKey = 'test-secret-2024';
            
            function log(message, type = 'info') {
                const timestamp = new Date().toLocaleTimeString();
                const prefix = type === 'error' ? '❌' : type === 'success' ? '✅' : 'ℹ️';
                output.textContent += `[${timestamp}] ${prefix} ${message}\\n`;
                output.scrollTop = output.scrollHeight;
            }
            
            function updateStatus(message, type = 'info') {
                status.textContent = message;
                status.className = 'status ' + type;
            }
            
            function clearOutput() {
                output.textContent = '';
                updateStatus('Вывод очищен', 'info');
            }
            
            async function runTests() {
                const btn = document.getElementById('runTests');
                btn.disabled = true;
                updateStatus('Запуск тестов...', 'loading');
                log('Начинаю запуск всех тестов...');
                
                try {
                    const formData = new FormData();
                    formData.append('action', 'run');
                    formData.append('key', secretKey);
                    
                    const response = await fetch('/secret-test/?key=' + secretKey, {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        updateStatus('Тесты успешно завершены', 'success');
                        log('=== STDOUT ===', 'info');
                        log(data.stdout || 'Нет вывода', 'info');
                        if (data.stderr) {
                            log('=== STDERR ===', 'error');
                            log(data.stderr, 'error');
                        }
                    } else {
                        updateStatus('Тесты завершились с ошибками', 'error');
                        log('=== ОШИБКА ===', 'error');
                        log(data.error || data.stderr || 'Неизвестная ошибка', 'error');
                        if (data.stdout) {
                            log('=== STDOUT ===', 'info');
                            log(data.stdout, 'info');
                        }
                    }
                } catch (error) {
                    updateStatus('Ошибка при запуске тестов', 'error');
                    log('Ошибка: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }
            
            async function runScenarios() {
                const btn = document.getElementById('runScenarios');
                btn.disabled = true;
                updateStatus('Запуск сценарных тестов...', 'loading');
                log('Начинаю запуск сценарных тестов...');
                
                try {
                    const response = await fetch('/secret-test/run-scenarios/?key=' + secretKey);
                    const data = await response.json();
                    
                    if (data.success) {
                        updateStatus('Сценарные тесты успешно завершены', 'success');
                        log('=== РЕЗУЛЬТАТЫ ===', 'info');
                        log(data.output || 'Нет вывода', 'info');
                    } else {
                        updateStatus('Сценарные тесты завершились с ошибками', 'error');
                        log('=== ОШИБКА ===', 'error');
                        log(data.error || 'Неизвестная ошибка', 'error');
                    }
                } catch (error) {
                    updateStatus('Ошибка при запуске сценарных тестов', 'error');
                    log('Ошибка: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }
            
            async function runIntegration() {
                const btn = document.getElementById('runIntegration');
                btn.disabled = true;
                updateStatus('Запуск интеграционных тестов...', 'loading');
                log('Начинаю запуск интеграционных тестов...');
                
                try {
                    const response = await fetch('/secret-test/run-integration/?key=' + secretKey);
                    const data = await response.json();
                    
                    if (data.success) {
                        updateStatus('Интеграционные тесты успешно завершены', 'success');
                        log('=== РЕЗУЛЬТАТЫ ===', 'info');
                        log(data.output || 'Нет вывода', 'info');
                    } else {
                        updateStatus('Интеграционные тесты завершились с ошибками', 'error');
                        log('=== ОШИБКА ===', 'error');
                        log(data.error || 'Неизвестная ошибка', 'error');
                    }
                } catch (error) {
                    updateStatus('Ошибка при запуске интеграционных тестов', 'error');
                    log('Ошибка: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }
            
            async function runE2E() {
                const btn = document.getElementById('runE2E');
                btn.disabled = true;
                updateStatus('Запуск E2E тестов...', 'loading');
                log('Начинаю запуск E2E тестов...');
                
                try {
                    const response = await fetch('/secret-test/run-e2e/?key=' + secretKey);
                    const data = await response.json();
                    
                    if (data.success) {
                        updateStatus('E2E тесты успешно завершены', 'success');
                        log('=== РЕЗУЛЬТАТЫ ===', 'info');
                        log(data.output || 'Нет вывода', 'info');
                    } else {
                        updateStatus('E2E тесты завершились с ошибками', 'error');
                        log('=== ОШИБКА ===', 'error');
                        log(data.error || 'Неизвестная ошибка', 'error');
                    }
                } catch (error) {
                    updateStatus('Ошибка при запуске E2E тестов', 'error');
                    log('Ошибка: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }
            
            async function runVisual() {
                const btn = document.getElementById('runVisual');
                btn.disabled = true;
                updateStatus('Запуск визуальных тестов... Браузер откроется автоматически', 'loading');
                log('Начинаю запуск визуальных тестов с Selenium...');
                log('⚠️ ВНИМАНИЕ: Браузер Chrome откроется и вы сможете наблюдать процесс тестирования!', 'info');
                
                try {
                    const response = await fetch('/secret-test/run-visual/?key=' + secretKey);
                    const data = await response.json();
                    
                    if (data.success) {
                        updateStatus('Визуальные тесты успешно завершены', 'success');
                        log('=== РЕЗУЛЬТАТЫ ===', 'info');
                        log(data.output || 'Нет вывода', 'info');
                        if (data.message) {
                            log(data.message, 'info');
                        }
                    } else {
                        updateStatus('Визуальные тесты завершились с ошибками', 'error');
                        log('=== ОШИБКА ===', 'error');
                        log(data.error || 'Неизвестная ошибка', 'error');
                    }
                } catch (error) {
                    updateStatus('Ошибка при запуске визуальных тестов', 'error');
                    log('Ошибка: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }
            
            async function checkStatus() {
                updateStatus('Проверка статуса...', 'loading');
                log('Проверяю статус системы тестирования...');
                
                try {
                    const formData = new FormData();
                    formData.append('action', 'status');
                    formData.append('key', secretKey);
                    
                    const response = await fetch('/secret-test/?key=' + secretKey, {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    updateStatus(data.message || 'Статус неизвестен', data.status === 'ready' ? 'success' : 'error');
                    log('Статус: ' + (data.message || 'неизвестен'), 'info');
                } catch (error) {
                    updateStatus('Ошибка при проверке статуса', 'error');
                    log('Ошибка: ' + error.message, 'error');
                }
            }
        </script>
    </body>
    </html>
    """
    return HttpResponse(html)


@csrf_exempt
def run_scenarios_tests(request):
    """Запуск сценарных тестов"""
    if not check_secret_key(request):
        return JsonResponse({'error': 'Access Denied'}, status=403)
    
    try:
        result = subprocess.run(
            ['pytest', 'transcribe/tests/test_scenarios.py', '--verbose', '-v', '-m', 'e2e'],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=settings.BASE_DIR
        )
        
        return JsonResponse({
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def run_integration_tests(request):
    """Запуск интеграционных тестов"""
    if not check_secret_key(request):
        return JsonResponse({'error': 'Access Denied'}, status=403)
    
    try:
        result = subprocess.run(
            ['pytest', 'transcribe/tests/test_integration.py', '--verbose', '-v', '-m', 'integration'],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=settings.BASE_DIR
        )
        
        return JsonResponse({
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def run_e2e_tests(request):
    """Запуск E2E тестов"""
    if not check_secret_key(request):
        return JsonResponse({'error': 'Access Denied'}, status=403)
    
    try:
        result = subprocess.run(
            ['pytest', 'transcribe/tests/test_e2e_scenarios.py', '--verbose', '-v', '-m', 'e2e'],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=settings.BASE_DIR
        )
        
        return JsonResponse({
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def run_visual_tests(request):
    """Запуск визуальных тестов с Selenium"""
    if not check_secret_key(request):
        return JsonResponse({'error': 'Access Denied'}, status=403)
    
    try:
        # Запускаем визуальные тесты
        # Используем DISPLAY из окружения или :0 для локального X server
        env = os.environ.copy()
        if 'DISPLAY' not in env:
            env['DISPLAY'] = ':0'  # Локальный X server
        
        # Запускаем pytest с флагом -s для видимого вывода
        result = subprocess.run(
            [
                'pytest', 
                'transcribe/tests/test_visual_scenarios.py', 
                '--verbose', 
                '-v', 
                '-m', 'visual', 
                '-s',  # Не перехватывать stdout/stderr
                '--tb=short'
            ],
            capture_output=True,
            text=True,
            timeout=1200,  # 20 минут для визуальных тестов
            cwd=settings.BASE_DIR,
            env=env
        )
        
        return JsonResponse({
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode,
            'message': 'Визуальные тесты завершены. Браузер должен был открыться автоматически для наблюдения процесса.'
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({
            'success': False,
            'error': 'Превышено время ожидания выполнения визуальных тестов'
        }, status=500)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Ошибка: {str(e)}. Убедитесь, что Chrome и ChromeDriver установлены на хосте.'
        }, status=500)

from django.urls import path
from . import views
from . import views_test

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload_file, name='upload_file'),
    path('login/', views.login_with_phrase, name='login_phrase'),
    path('logout/', views.logout_phrase, name='logout_phrase'),
    path('transcription/<int:transcription_id>/', views.transcription_detail, name='transcription_detail'),
    path('public/<str:public_token>/', views.transcription_detail, name='transcription_public'),
    path('transcription/<int:transcription_id>/status/', views.transcription_status, name='transcription_status'),
    path('transcription/<int:transcription_id>/download-text/', views.download_text, name='download_text'),
    path('transcription/<int:transcription_id>/download-screenshots/', views.download_screenshots, name='download_screenshots'),
    path('public/<str:public_token>/download-text/', views.download_text, {'public_token': True}, name='download_text_public'),
    path('public/<str:public_token>/download-screenshots/', views.download_screenshots, {'public_token': True}, name='download_screenshots_public'),
    path('session/<str:upload_session>/download-text/', views.download_session_text, name='download_session_text'),
    path('payment/', views.process_payment, name='process_payment'),
    path('transcription/<int:transcription_id>/retranscribe/', views.retranscribe, name='retranscribe'),
    path('clear-disk/', views.clear_disk, name='clear_disk'),
    # Секретная страница тестирования
    path('secret-test/', views_test.secret_test_page, name='secret_test'),
    path('secret-test/run-scenarios/', views_test.run_scenarios_tests, name='run_scenarios'),
    path('secret-test/run-integration/', views_test.run_integration_tests, name='run_integration'),
    path('secret-test/run-e2e/', views_test.run_e2e_tests, name='run_e2e'),
    path('secret-test/run-visual/', views_test.run_visual_tests, name='run_visual'),
]


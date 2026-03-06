from django.urls import path
from . import views

app_name = 'recordings'

urlpatterns = [
    path('', views.index, name='index'),
    path('manifest.json', views.manifest_view, name='manifest'),
    path('sw.js', views.service_worker_view, name='service_worker'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('pilot_integration/', views.pilot_integration, name='pilot_integration'),
    path('pilot-login/', views.pilot_login, name='pilot_login'),
    path('admin-passwords/', views.admin_passwords, name='admin_passwords'),
    path('api/check-email/', views.api_check_email, name='api_check_email'),
    path('api/ask-transcription/', views.api_ask_transcription, name='api_ask_transcription'),
    path('api/request-tg-verify/', views.api_request_tg_verify, name='api_request_tg_verify'),
    path('api/check-tg-verify/', views.api_check_tg_verify, name='api_check_tg_verify'),
    path('about/', views.description_view, name='description'),
    path('logs/', views.log_list, name='logs'),
    path('uploads/', views.uploads_page, name='uploads'),
    path('ocr/', views.ocr_page, name='ocr'),
    path('ocr/<int:job_id>/', views.ocr_job_detail, name='ocr_job_detail'),
    path('ocr/share/<uuid:token>/', views.ocr_share, name='ocr_share'),
    path('space/', views.space_members, name='space_members'),
    path('api/space/<uuid:api_key>/ocr/', views.api_space_ocr, name='api_space_ocr'),
    path('run-transcription/', views.run_transcription, name='run_transcription'),
    path('support/<int:recording_id>/', views.support_request, name='support_request'),
    path('r/<int:recording_id>/', views.recording_detail, name='recording_detail'),
    path('r/<int:recording_id>/run/', views.run_recording_transcription, name='run_recording_transcription'),
    path('r/<int:recording_id>/download/', views.download_recording, name='download_recording'),
    path('r/<int:recording_id>/download-txt/', views.download_transcription, name='download_transcription'),
    path('r/<int:recording_id>/comment/', views.add_comment, name='add_comment'),
    path('r/<int:recording_id>/tag/', views.set_recording_tag, name='set_recording_tag'),
    # Share (public link, no auth required)
    path('r/<int:recording_id>/share/', views.create_share_token, name='create_share_token'),
    path('share/<uuid:token>/', views.shared_recording, name='shared_recording'),
    # Telemetry (CSRF-exempt)
    path('api/log-screen/', views.log_screen, name='log_screen'),
    # OCR Public API
    path('api/ocr/submit/', views.api_ocr_submit, name='api_ocr_submit'),
    path('api/ocr/status/<int:task_id>/', views.api_ocr_status, name='api_ocr_status'),
    # V1 API
    path('api/v1/org/', views.api_v1_org_create, name='api_v1_org_create'),
    path('api/v1/ocr/', views.api_v1_ocr_submit, name='api_v1_ocr_submit'),
    path('api/v1/ocr/<int:task_id>/', views.api_v1_ocr_status, name='api_v1_ocr_status'),
    path('magic-login/<uuid:token>/', views.magic_login, name='magic_login'),
    path('cabinet/', views.cabinet, name='cabinet'),
    path('api/dadata-suggest/', views.api_dadata_suggest, name='api_dadata_suggest'),
    # Регистрация организаций и Telegram webhook
    path('org-register/', views.org_register, name='org_register'),
    path('tg-webhook/<str:secret>/', views.tg_webhook, name='tg_webhook'),
    path('landing/', views.landing, name='landing'),
]

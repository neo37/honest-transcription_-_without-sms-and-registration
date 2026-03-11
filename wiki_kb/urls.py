from django.urls import path
from . import views

app_name = 'wiki_kb'

urlpatterns = [
    path('', views.kb_index, name='kb_index'),
    path('new/', views.article_create, name='article_create'),
    path('search/', views.wiki_search, name='wiki_search'),
    path('share/<uuid:token>/', views.article_public, name='article_public'),
    path('from-recording/<int:recording_id>/', views.wiki_from_recording, name='wiki_from_recording'),
    path('<slug:slug>/', views.article_detail, name='article_detail'),
    path('<slug:slug>/edit/', views.article_edit, name='article_edit'),
    path('<slug:slug>/delete/', views.article_delete, name='article_delete'),
    path('<slug:slug>/history/', views.article_history, name='article_history'),
    path('<slug:slug>/share-toggle/', views.article_share_toggle, name='article_share_toggle'),
    path('<slug:slug>/pick-recordings/', views.article_pick_recordings, name='article_pick_recordings'),
    path('<slug:slug>/move/', views.article_move, name='article_move'),
    path('<slug:slug>/merge/', views.article_merge, name='article_merge'),
]

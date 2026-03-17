from django.contrib import admin
from django.urls import path, include
from django.views.defaults import page_not_found

from recordings.admin import admin_worker_status, admin_tg_broadcast
from recordings import views as rec_views
from wiki_kb import api as wiki_api

handler404 = 'django.views.defaults.page_not_found'

urlpatterns = [
    path('admin/worker-status/', admin_worker_status, name='admin_worker_status'),
    path('admin/recordings/siteuser/tg-broadcast/', admin_tg_broadcast, name='admin_tg_broadcast'),
    path('admin/yadoc-import/', rec_views.yadoc_import, name='yadoc_import'),
    path('admin/yadoc-import/fetch-tree/', rec_views.yadoc_fetch_tree, name='yadoc_fetch_tree'),
    path('admin/yadoc-import/start/', rec_views.yadoc_start_import, name='yadoc_start_import'),
    path('admin/yadoc-import/stream/', rec_views.yadoc_stream, name='yadoc_stream'),
    path('admin/', admin.site.urls),
    path('kb/', include('wiki_kb.urls')),
    # Wiki REST API
    path('api/wiki/', wiki_api.api_wiki_list),
    path('api/wiki/tree/', wiki_api.api_wiki_tree),
    path('api/wiki/tree/<slug:slug>/', wiki_api.api_wiki_tree_from),
    path('api/wiki/search/', wiki_api.api_wiki_search),
    path('api/wiki/<slug:slug>/', wiki_api.api_wiki_detail),
    path('api/wiki/<slug:slug>/move/', wiki_api.api_wiki_move),
    path('', include('recordings.urls')),
]

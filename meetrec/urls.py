from django.contrib import admin
from django.urls import path, include

from recordings.admin import admin_worker_status, admin_tg_broadcast
from wiki_kb import api as wiki_api

urlpatterns = [
    path('admin/worker-status/', admin_worker_status, name='admin_worker_status'),
    path('admin/recordings/siteuser/tg-broadcast/', admin_tg_broadcast, name='admin_tg_broadcast'),
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

from django.contrib import admin
from django.urls import path, include

from recordings.admin import admin_worker_status

urlpatterns = [
    path('admin/worker-status/', admin_worker_status, name='admin_worker_status'),
    path('admin/', admin.site.urls),
    path('kb/', include('wiki_kb.urls')),
    path('', include('recordings.urls')),
]

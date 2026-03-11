from django.urls import path
from . import views

app_name = 'knowledge_graph'

urlpatterns = [
    path('', views.graph_page, name='graph'),
    path('api/data/', views.graph_data, name='graph_data'),
    path('api/rebuild/', views.rebuild_graph, name='rebuild_graph'),
]

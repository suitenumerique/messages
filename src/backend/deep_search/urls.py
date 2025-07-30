"""
URL configuration for the deep_search module.
"""

from django.urls import path
from . import views

app_name = 'deep_search'

urlpatterns = [
    # AI-powered intelligent email search
    path('intelligent-search/', views.intelligent_search_api, name='intelligent_search'),
    
    # General conversation endpoint
    path('conversation/', views.conversation_api, name='conversation'),
    
    # Deep search status and configuration
    path('status/', views.chatbot_status_api, name='chatbot_status'),
    
]

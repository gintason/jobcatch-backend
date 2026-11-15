from django.urls import path

from .views import AIChatView, AIHistoryView

urlpatterns = [
    path("ai/chat/", AIChatView.as_view(), name="ai-chat"),
    path("ai/history/", AIHistoryView.as_view(), name="ai-history"),
]

"""AI chatbot storage: knowledge-base chunks and per-user chat history.

Embeddings live in a JSONField (list[float]) with cosine similarity computed in
Python. This avoids a pgvector/PostGIS image change; swap to pgvector later
behind rag.retrieve_context() when the KB grows large.
"""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class KBChunk(BaseModel):
    title = models.CharField(max_length=200)
    content = models.TextField()
    embedding = models.JSONField(default=list, blank=True)  # list[float]

    def __str__(self):
        return self.title


class AIConversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_conversations"
    )

    def __str__(self):
        return f"AIConversation<{self.id}> {self.user.email}"


class AIMessage(BaseModel):
    conversation = models.ForeignKey(
        AIConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20)  # user | assistant
    content = models.TextField()

    class Meta(BaseModel.Meta):
        ordering = ("created_at",)

from django.contrib import admin

from .models import AIConversation, AIMessage, KBChunk


@admin.register(KBChunk)
class KBChunkAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    search_fields = ("title", "content")


admin.site.register(AIConversation)
admin.site.register(AIMessage)

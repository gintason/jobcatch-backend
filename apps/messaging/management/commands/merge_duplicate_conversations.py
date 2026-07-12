"""
Merge duplicate conversations created before get-or-create was in place.

Earlier, each participant clicking "Message" created a separate Conversation for
the same booking/job, so the two sides ended up in different rooms. This folds
those duplicates into the oldest thread and moves all messages across.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.messaging.models import Conversation, Message


class Command(BaseCommand):
    help = "Merge duplicate conversations sharing the same booking/job + participants."

    @transaction.atomic
    def handle(self, *args, **options):
        groups = defaultdict(list)
        for conv in Conversation.objects.prefetch_related("participants"):
            key = (
                conv.booking_id,
                conv.job_id,
                frozenset(u.id for u in conv.participants.all()),
            )
            groups[key].append(conv)

        merged = 0
        for convs in groups.values():
            if len(convs) < 2:
                continue
            convs.sort(key=lambda c: c.created_at)
            keeper, dupes = convs[0], convs[1:]
            for dupe in dupes:
                Message.objects.filter(conversation=dupe).update(conversation=keeper)
                dupe.delete()
                merged += 1

        self.stdout.write(
            self.style.SUCCESS(f"Merged {merged} duplicate conversation(s).")
        )

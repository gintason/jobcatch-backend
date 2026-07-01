"""Reviews & ratings — one review per completed booking."""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import BaseModel


class Review(BaseModel):
    booking = models.OneToOneField(
        "bookings.Booking", on_delete=models.CASCADE, related_name="review"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews_written"
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews_received"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True)
    is_visible = models.BooleanField(default=True)  # moderation

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["target", "is_visible"])]

    def __str__(self):
        return f"{self.rating}★ for {self.target.email}"

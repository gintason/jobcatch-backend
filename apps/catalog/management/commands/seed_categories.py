"""
Seed the two category taxonomies.

Home services and job listings are separate marketplaces with separate
vocabularies — a customer booking a plumber and an employer hiring a petroleum
engineer share no categories. Idempotent: safe to re-run.
"""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.catalog.models import Category, CategoryKind

HOME_SERVICES = [
    "Plumber",
    "Electrician",
    "Carpenter",
    "Painter",
    "Welder",
    "AC Technician",
    "Generator Technician",
    "CCTV Installer",
    "Solar Installer",
    "Cleaner",
    "Housekeeper",
    "Chef/Cook",
    "Driver",
    "Gardener",
    "Tailor/Fashion Designer",
    "Makeup Artist",
    "Hair Stylist/Barber",
    "Photographer",
    "Event Planner",
    "Security Guard",
    "Laundry Service",
    "Pest Control Technician",
    "Furniture Installer",
    "Appliance Repair Technician",
    "Mason/Tiler",
    "POP Installer",
    "I.T Support",
    # --- added on client request ---
    "Seamstress",
    "Video Coverage",
    "Roof Installer",
    "Electric Fence Wire",
    "Disc Jockey (DJ)",
    "Concrete Stamping",
    "Artist",
    "Equipment Rentals",
    "Engineers",
    "Architects",
    # --- added on client request (batch 2) ---
    "Printer",
    "Mechanic",
    "Machine Operator",
    "Iron Bender",
    "Aluminium Jobs",
    "Panel Beater",
    # --- added on client request (batch 3) ---
    "Borehole Drilling and Installation",
    "Cleaning and Fumigation",
    "Intellectual Property Protection and Registration",
]

JOB_CATEGORIES = [
    "Information Technology (IT) & Software Development",
    "Engineering",
    "Healthcare & Medical",
    "Education & Training",
    "Banking, Finance & Accounting",
    "Sales & Marketing",
    "Customer Service & Support",
    "Human Resources (HR)",
    "Administration & Office Management",
    "Legal Services",
    "Government & Public Sector",
    "Construction & Building",
    "Real Estate & Property Management",
    "Manufacturing & Production",
    "Agriculture & Farming",
    "Oil & Gas",
    "Energy & Utilities",
    "Telecommunications",
    "Transportation & Logistics",
    "Procurement & Supply Chain",
    "Retail & Wholesale",
    "E-commerce",
    "Hospitality & Tourism",
    "Food & Beverage",
    "Fashion & Beauty",
    "Media & Communications",
    "Advertising & Public Relations",
    "Creative Arts & Design",
    "Entertainment & Events",
    "Photography & Videography",
    "Writing & Content Creation",
    "Research & Development",
    "Science & Biotechnology",
    "Environmental Services",
    "Security & Safety Services",
    "Non-Governmental Organizations (NGOs) & Non-Profit",
    "International Development",
    "Consulting & Business Strategy",
    "Insurance",
    "Aviation & Aerospace",
    "Marine & Shipping",
    "Mining & Natural Resources",
    "Sports & Fitness",
    "Religious & Faith-Based Organizations",
    "Domestic & Household Services",
    "Cleaning & Janitorial Services",
    "Skilled Trades & Artisan Services",
    "Automotive & Mechanics",
    "Laundry & Dry Cleaning Services",
    "Beauty & Personal Care Services",
    "Childcare & Elderly Care",
    "Freelance & Remote Jobs",
    "Internships & Graduate Trainee Programs",
    "Part-Time Jobs",
    "Contract Jobs",
    "Temporary Jobs",
    "Volunteer Opportunities",
    "Apprenticeships",
    "Remote Customer Support",
    "Other Jobs",
]


class Command(BaseCommand):
    help = "Seed home-service and job-listing categories."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete categories not in these lists (only if unused by any service/job).",
        )

    def handle(self, *args, **options):
        created = {"home_service": 0, "job": 0}

        for names, kind in ((HOME_SERVICES, CategoryKind.HOME_SERVICE),
                            (JOB_CATEGORIES, CategoryKind.JOB)):
            for name in names:
                slug = slugify(name)
                # The DB unique constraint is on (kind, slug), so look up by slug
                # — not name — to avoid a UniqueViolation when two different names
                # slugify to the same value (e.g. "I.T Support" and "IT Support"
                # both -> "it-support"). Existing rows are left untouched.
                _, made = Category.objects.get_or_create(
                    kind=kind, slug=slug, defaults={"name": name},
                )
                if made:
                    created[kind] += 1

        self.stdout.write(self.style.SUCCESS(
            f"Home services: +{created['home_service']} "
            f"(total {Category.objects.filter(kind=CategoryKind.HOME_SERVICE).count()})"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Job listings: +{created['job']} "
            f"(total {Category.objects.filter(kind=CategoryKind.JOB).count()})"
        ))

        if options["prune"]:
            self._prune()

    def _prune(self):
        """Remove stale categories, but never one that's still in use."""
        keep_home = set(HOME_SERVICES)
        keep_job = set(JOB_CATEGORIES)
        removed, kept = 0, []

        for cat in Category.objects.all():
            wanted = keep_home if cat.kind == CategoryKind.HOME_SERVICE else keep_job
            if cat.name in wanted:
                continue
            if cat.services.exists() or getattr(cat, "jobs", cat.services.none()).exists():
                kept.append(cat.name)
                continue
            cat.delete()
            removed += 1

        self.stdout.write(self.style.WARNING(f"Pruned {removed} unused category(ies)."))
        if kept:
            self.stdout.write(self.style.WARNING(
                "Kept (still in use — reassign before pruning): " + ", ".join(kept)
            ))

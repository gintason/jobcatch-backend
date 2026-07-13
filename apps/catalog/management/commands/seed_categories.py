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
    help = "Seed home-service and job-listing categories (idempotent)."

    def handle(self, *args, **options):
        for kind, names in (
            (CategoryKind.HOME_SERVICE, HOME_SERVICES),
            (CategoryKind.JOB, JOB_CATEGORIES),
        ):
            created = 0
            for name in names:
                _, was_created = Category.objects.get_or_create(
                    kind=kind, name=name, defaults={"slug": slugify(name)},
                )
                created += int(was_created)
            total = Category.objects.filter(kind=kind).count()
            label = kind.label
            self.stdout.write(
                self.style.SUCCESS(f"{label}: +{created} (total {total})")
            )

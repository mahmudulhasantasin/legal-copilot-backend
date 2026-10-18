"""
One-time seed for default credit packages.
Run: python -m scripts.seed_packages
"""
import asyncio
import os
import sys

# Ensure the app directory is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.playbook import CreditPackage, CreditPackageTier
from app.core.pricing import DEFAULT_PACKAGES


async def seed():
    async with AsyncSessionLocal() as db:
        # Check if packages already exist to prevent duplicates
        existing = (await db.execute(select(CreditPackage))).scalars().all()
        if existing:
            print(f"⚠️  {len(existing)} packages already exist. Skipping seed.")
            return

        for pkg_data in DEFAULT_PACKAGES:
            db.add(CreditPackage(
                name=pkg_data["name"],
                tier=CreditPackageTier(pkg_data["tier"]),
                description=pkg_data["description"],
                price_bdt=pkg_data["price_bdt"],
                base_credits=pkg_data["base_credits"],
                bonus_credits=pkg_data["bonus_credits"],
                applies_to=pkg_data["applies_to"],
                display_order=pkg_data["display_order"],
                badge_text=pkg_data["badge_text"],
                is_active=True,
            ))
        await db.commit()
        print(f"✅ Seeded {len(DEFAULT_PACKAGES)} packages successfully.")


if __name__ == "__main__":
    asyncio.run(seed())
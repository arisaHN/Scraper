import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SiteEnum(str, enum.Enum):
    trustpilot = "trustpilot"
    amazon = "amazon"
    google = "google"
    bazaarvoice = "bazaarvoice"


class RunStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failed = "failed"
    partial = "partial"


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    products: Mapped[list["Product"]] = relationship(back_populates="brand")
    scrape_runs: Mapped[list["ScrapeRun"]] = relationship(back_populates="brand")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("source_site", "external_id", name="uq_product_site_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_site: Mapped[str] = mapped_column(SAEnum(SiteEnum), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    external_id: Mapped[Optional[str]] = mapped_column(String(512))
    retailer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    brand: Mapped["Brand"] = relationship(back_populates="products")
    reviews: Mapped[list["Review"]] = relationship(back_populates="product")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "source_site", "external_review_id", name="uq_review_site_external"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    source_site: Mapped[str] = mapped_column(SAEnum(SiteEnum), nullable=False)
    external_review_id: Mapped[Optional[str]] = mapped_column(String(512))
    author: Mapped[Optional[str]] = mapped_column(Text)
    rating: Mapped[Optional[float]] = mapped_column(Float)
    title: Mapped[Optional[str]] = mapped_column(Text)
    text: Mapped[Optional[str]] = mapped_column(Text)
    review_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    product: Mapped["Product"] = relationship(back_populates="reviews")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False)
    site: Mapped[str] = mapped_column(SAEnum(SiteEnum), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(RunStatus), default=RunStatus.running, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviews_found: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    brand: Mapped["Brand"] = relationship(back_populates="scrape_runs")

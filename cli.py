import click

from src.config import settings
from src.database import get_session, init_db
from src.models import Brand, Product, Review


@click.group()
def cli():
    """Multi-site review scraper."""
    if settings.DATABASE_URL:
        init_db()


@cli.command("add-brand")
@click.argument("name")
def add_brand(name: str):
    """Register a brand and run an initial scrape across all sites."""
    with get_session() as session:
        existing = session.query(Brand).filter_by(name=name).first()
        if existing:
            click.echo(f"Brand '{name}' already exists (id={existing.id}).")
            return
        brand = Brand(name=name)
        session.add(brand)
        session.flush()
        brand_id = brand.id

    click.echo(f"Added brand '{name}' (id={brand_id}). Starting initial scrape...")
    from src.runner import run_brand
    from src.scrapers import SCRAPER_REGISTRY

    for site, ScraperClass in SCRAPER_REGISTRY.items():
        click.echo(f"  [{site}] scraping...")
        try:
            count = run_brand(brand_id, name, site, ScraperClass)
            click.echo(f"  [{site}] done — {count} reviews saved.")
        except Exception as e:
            click.echo(f"  [{site}] failed: {e}", err=True)


@cli.command("scrape")
@click.argument("brand")
@click.option("--site", default=None, help="Scrape only this site (trustpilot, amazon, google, bazaarvoice)")
def scrape(brand: str, site: str):
    """On-demand scrape for a brand."""
    from src.runner import run_all_sites, run_brand
    from src.scrapers import SCRAPER_REGISTRY

    with get_session() as session:
        b = session.query(Brand).filter_by(name=brand).first()
        if not b:
            click.echo(f"Brand '{brand}' not found. Use 'add-brand' first.", err=True)
            return
        brand_id, brand_name = b.id, b.name

    if site:
        if site not in SCRAPER_REGISTRY:
            click.echo(f"Unknown site '{site}'. Choose from: {', '.join(SCRAPER_REGISTRY)}", err=True)
            return
        ScraperClass = SCRAPER_REGISTRY[site]
        click.echo(f"Scraping {site} for '{brand_name}'...")
        try:
            count = run_brand(brand_id, brand_name, site, ScraperClass)
            click.echo(f"Done — {count} reviews saved.")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        results = run_all_sites(brand_name)
        for s, count in results.items():
            status = f"{count} reviews" if count >= 0 else "FAILED"
            click.echo(f"  [{s}] {status}")


@cli.command("remove-brand")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def remove_brand(name: str, yes: bool):
    """Remove a brand and all its reviews, products, and scrape runs."""
    with get_session() as session:
        brand = session.query(Brand).filter_by(name=name).first()
        if not brand:
            click.echo(f"Brand '{name}' not found.", err=True)
            return

        review_count = (
            session.query(Review)
            .join(Product, Review.product_id == Product.id)
            .filter(Product.brand_id == brand.id)
            .count()
        )

    if not yes:
        click.confirm(
            f"Delete brand '{name}' and all {review_count} reviews? This cannot be undone.",
            abort=True,
        )

    from src.models import ScrapeRun

    with get_session() as session:
        brand = session.query(Brand).filter_by(name=name).first()
        products = session.query(Product).filter_by(brand_id=brand.id).all()
        product_ids = [p.id for p in products]

        if product_ids:
            session.query(Review).filter(Review.product_id.in_(product_ids)).delete(synchronize_session=False)
        session.query(Product).filter_by(brand_id=brand.id).delete(synchronize_session=False)
        session.query(ScrapeRun).filter_by(brand_id=brand.id).delete(synchronize_session=False)
        session.delete(brand)

    click.echo(f"Removed brand '{name}' and {review_count} reviews.")


@cli.command("list-brands")
def list_brands():
    """Show all tracked brands with review counts."""
    with get_session() as session:
        brands = session.query(Brand).order_by(Brand.name).all()
        if not brands:
            click.echo("No brands tracked yet. Use 'add-brand <name>' to start.")
            return
        click.echo(f"{'ID':>4}  {'Brand':<30}  Reviews")
        click.echo("-" * 46)
        for b in brands:
            count = (
                session.query(Review)
                .join(Product, Review.product_id == Product.id)
                .filter(Product.brand_id == b.id)
                .count()
            )
            click.echo(f"{b.id:>4}  {b.name:<30}  {count:>6}")


@cli.command("export")
@click.argument("brand")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv", show_default=True)
@click.option("--output", "-o", default=None, help="Output file path")
def export(brand: str, fmt: str, output: str):
    """Export all reviews for a brand to CSV or JSON."""
    from src.exporter import export_brand
    try:
        path = export_brand(brand, fmt, output)
        click.echo(f"Exported to {path}")
    except ValueError as e:
        click.echo(str(e), err=True)


if __name__ == "__main__":
    cli()

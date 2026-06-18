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

    for registry_key in SCRAPER_REGISTRY:
        click.echo(f"  [{registry_key}] scraping...")
        try:
            count = run_brand(brand_id, name, registry_key)
            click.echo(f"  [{registry_key}] done — {count} reviews saved.")
        except Exception as e:
            click.echo(f"  [{registry_key}] failed: {e}", err=True)


@cli.command("scrape")
@click.argument("brand")
@click.option("--site", default=None, help="Scrape only this site (e.g. bazaarvoice_douglas, trustpilot, amazon, google)")
@click.option("--product-id", default=None, help="Scrape only this product external ID (requires --site)")
def scrape(brand: str, site: str, product_id: str):
    """On-demand scrape for a brand."""
    from src.runner import run_all_sites, run_brand, run_single_product
    from src.scrapers import SCRAPER_REGISTRY

    if product_id and not site:
        click.echo("--product-id requires --site to be specified.", err=True)
        return

    with get_session() as session:
        b = session.query(Brand).filter_by(name=brand).first()
        if not b:
            click.echo(f"Brand '{brand}' not found. Use 'add-brand' first.", err=True)
            return
        brand_id, brand_name = b.id, b.name

    if site and site not in SCRAPER_REGISTRY:
        click.echo(f"Unknown site '{site}'. Choose from: {', '.join(SCRAPER_REGISTRY)}", err=True)
        return

    if product_id:
        click.echo(f"Scraping product '{product_id}' from {site}...")
        try:
            count = run_single_product(brand_id, brand_name, site, product_id)
            click.echo(f"Done — {count} reviews saved.")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    elif site:
        click.echo(f"Scraping {site} for '{brand_name}'...")
        try:
            count = run_brand(brand_id, brand_name, site)
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

    from src.models import ScrapeRun, SephoraBackfillCursor

    with get_session() as session:
        brand = session.query(Brand).filter_by(name=name).first()
        products = session.query(Product).filter_by(brand_id=brand.id).all()
        product_ids = [p.id for p in products]

        if product_ids:
            session.query(SephoraBackfillCursor).filter(
                SephoraBackfillCursor.product_id.in_(product_ids)
            ).delete(synchronize_session=False)
            session.query(Review).filter(Review.product_id.in_(product_ids)).delete(synchronize_session=False)
        session.query(Product).filter_by(brand_id=brand.id).delete(synchronize_session=False)
        session.query(ScrapeRun).filter_by(brand_id=brand.id).delete(synchronize_session=False)
        session.delete(brand)

    click.echo(f"Removed brand '{name}' and {review_count} reviews.")


@cli.command("remove-retailer")
@click.argument("retailer")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def remove_retailer(retailer: str, yes: bool):
    """Remove all products, reviews, and scrape-run history for a retailer (across all brands)."""
    from src.models import ScrapeRun, SephoraBackfillCursor

    with get_session() as session:
        products = session.query(Product).filter_by(retailer=retailer).all()
        if not products:
            click.echo(f"No products found for retailer '{retailer}'.", err=True)
            return

        product_ids = [p.id for p in products]
        brand_ids = sorted({p.brand_id for p in products})
        sites = sorted({p.source_site for p in products})

        review_count = (
            session.query(Review).filter(Review.product_id.in_(product_ids)).count()
        )

        # Other retailers on the same brand/site share ScrapeRun bookkeeping (site isn't
        # retailer-specific) — warn if deleting ScrapeRun rows here would also reset their
        # incremental-scrape cursor.
        shared = (
            session.query(Product.brand_id, Product.source_site)
            .filter(
                Product.source_site.in_(sites),
                Product.retailer != retailer,
                Product.brand_id.in_(brand_ids),
            )
            .distinct()
            .all()
        )

    if shared:
        click.echo(
            "Warning: these brand/site combos have OTHER retailers sharing scrape_runs "
            "bookkeeping — their incremental-scrape cursor will also reset:"
        )
        for brand_id, site in shared:
            click.echo(f"  brand_id={brand_id} site={site}")

    if not yes:
        click.confirm(
            f"Delete retailer '{retailer}': {len(products)} products, {review_count} reviews, "
            f"and scrape_runs for {len(brand_ids)} brand(s)? This cannot be undone.",
            abort=True,
        )

    with get_session() as session:
        session.query(SephoraBackfillCursor).filter(
            SephoraBackfillCursor.product_id.in_(product_ids)
        ).delete(synchronize_session=False)
        session.query(Review).filter(Review.product_id.in_(product_ids)).delete(synchronize_session=False)
        session.query(Product).filter_by(retailer=retailer).delete(synchronize_session=False)
        session.query(ScrapeRun).filter(
            ScrapeRun.brand_id.in_(brand_ids), ScrapeRun.site.in_(sites)
        ).delete(synchronize_session=False)

    click.echo(f"Removed retailer '{retailer}': {len(products)} products, {review_count} reviews.")


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


@cli.command("list-products")
@click.argument("brand")
@click.option("--search", default=None, help="Filter products by name (case-insensitive)")
def list_products(brand: str, search: str):
    """List all products for a brand with review counts."""
    with get_session() as session:
        b = session.query(Brand).filter_by(name=brand).first()
        if not b:
            click.echo(f"Brand '{brand}' not found.", err=True)
            return
        query = session.query(Product).filter_by(brand_id=b.id)
        if search:
            query = query.filter(Product.name.ilike(f"%{search}%"))
        products = query.order_by(Product.source_site, Product.name).all()
        if not products:
            click.echo("No products found.")
            return
        click.echo(f"{'ID':>6}  {'Site':<14}  {'Retailer':<12}  {'Reviews':>7}  Name")
        click.echo("-" * 90)
        for p in products:
            count = session.query(Review).filter_by(product_id=p.id).count()
            retailer = p.retailer or ""
            click.echo(f"{p.id:>6}  {p.source_site:<14}  {retailer:<12}  {count:>7}  {p.name[:45]}")


@cli.command("export")
@click.argument("brand")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv", show_default=True)
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--product", default=None, help="Filter by product name (case-insensitive substring match)")
@click.option("--product-id", "product_id", default=None, type=int, help="Filter by exact product DB ID (from list-products)")
def export(brand: str, fmt: str, output: str, product: str, product_id: int):
    """Export all reviews for a brand to CSV or JSON."""
    from src.exporter import export_brand
    try:
        path = export_brand(brand, fmt, output, product_filter=product, product_id_filter=product_id)
        click.echo(f"Exported to {path}")
    except ValueError as e:
        click.echo(str(e), err=True)


if __name__ == "__main__":
    cli()

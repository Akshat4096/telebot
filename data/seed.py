"""
Seed the catalogue with real Indian kirana SKUs and correct-ish GST slabs/HSN
codes, per the assignment's domain requirements. Run once via `python -m data.seed`.

GST slab notes (illustrative, matches current retail practice as of mid-2025):
  0%   — loose/unbranded staples: loose atta, loose rice, loose dal, fresh produce
  5%   — packaged food staples: branded atta, salt is actually 0% (essential),
         edible oil, tea
  12%  — butter, ghee, processed foods
  18%  — detergents, soaps, packaged snacks/biscuits (varies by exact product)
"""
from db.db import init_db
from tools.inventory import add_product


PRODUCTS = [
    # name, brand, category, unit, is_loose, hsn, gst, cost, mrp, qty, reorder
    ("Aashirvaad Atta 5kg", "Aashirvaad", "atta", "packet", False, "1101", 5, 210, 245, 40, 10),
    ("Tata Salt 1kg", "Tata", "salt", "packet", False, "2501", 0, 18, 22, 60, 15),
    ("Amul Butter 100g", "Amul", "dairy", "packet", False, "0405", 12, 48, 62, 30, 8),
    ("Fortune Sunflower Oil 1L", "Fortune", "oil", "packet", False, "1512", 5, 118, 145, 25, 6),
    ("Maggi 70g", "Nestle", "instant_food", "packet", False, "1902", 12, 10, 14, 100, 20),
    ("Parle-G", "Parle", "biscuits", "packet", False, "1905", 18, 8, 10, 120, 25),
    ("Surf Excel 1kg", "HUL", "detergent", "packet", False, "3402", 18, 95, 130, 20, 5),
    ("Loose Sugar", None, "staples", "kg", True, "1701", 0, 40, 45, 80, 15),
    ("Loose Rice", None, "staples", "kg", True, "1006", 0, 38, 48, 100, 20),
    ("Loose Toor Dal", None, "staples", "kg", True, "0713", 0, 110, 135, 50, 10),
]


def run():
    init_db()
    for name, brand, category, unit, is_loose, hsn, gst, cost, mrp, qty, reorder in PRODUCTS:
        try:
            add_product(
                name=name, brand=brand, category=category, unit=unit, is_loose=is_loose,
                hsn_code=hsn, gst_rate=gst, cost_price=cost, mrp=mrp,
                opening_quantity=qty, reorder_level=reorder,
            )
            print(f"added: {name}")
        except Exception as e:
            print(f"skip {name}: {e}")


if __name__ == "__main__":
    run()

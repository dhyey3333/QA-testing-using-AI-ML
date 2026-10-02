"""The demo shop's data. All fake; the account works nowhere but here."""

# A fake number in the 98765 range, which Indian docs and tests use the way the US uses 555.
TEST_USER = {"email": "shopper@kulhad.test", "password": "chai-time-42", "name": "Test Shopper",
             "phone": "9876543210"}

PRODUCTS = [
    {"id": "chai", "name": "Masala Chai (250 g)", "price": 180,
     "blurb": "Strong CTC with cardamom, ginger and clove."},
    {"id": "coffee", "name": "Filter Coffee (200 g)", "price": 240,
     "blurb": "80:20 coffee and chicory, ground for a steel filter."},
    {"id": "kulhad", "name": "Clay Kulhad (set of 6)", "price": 420,
     "blurb": "Unglazed clay cups. The chai tastes better, we promise."},
    {"id": "tumbler", "name": "Steel Tumbler & Dabara", "price": 350,
     "blurb": "For cooling filter coffee the proper way."},
    {"id": "cookies", "name": "Jaggery Cookies", "price": 150,
     "blurb": "Whole wheat, jaggery, a pinch of salt."},
    {"id": "kettle", "name": "Electric Kettle 1.5 L", "price": 1299,
     "blurb": "Boils in three minutes. Switches itself off."},
]

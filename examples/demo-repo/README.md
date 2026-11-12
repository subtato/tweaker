# Acme Store (Tweaker demo repository)

A deliberately small repository used by Tweaker's demo mode. It contains two
bugs that match the seeded Sentry issues:

- `app/checkout.py` raises `KeyError` for an unknown coupon code.
- `web/src/cart.tsx` dereferences an optional product.

Run the tests with:

```bash
python -m pytest tests -q
```

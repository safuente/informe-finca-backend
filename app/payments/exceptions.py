class PaymentError(Exception):
    """Base error for the payments domain."""


class InvalidWebhookSignature(PaymentError):
    """The request did not come from Stripe (or the signing secret is wrong)."""


class StripeNotConfigured(PaymentError):
    """Stripe keys are missing; Checkout cannot be created from the API."""

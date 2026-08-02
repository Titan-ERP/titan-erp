import json
import logging

from odoo import http
from odoo.http import request

from ..utils import verify_stripe_signature

_logger = logging.getLogger(__name__)


class SouthernStripeTerminalWebhook(http.Controller):
    _webhook_url = "/southern/stripe-terminal/webhook"

    @http.route(_webhook_url, type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def stripe_terminal_webhook(self):
        payload = request.httprequest.get_data(cache=False)
        signature = request.httprequest.headers.get("Stripe-Signature", "")
        providers = (
            request.env["southern.stripe.terminal.config"].sudo().search([("active", "=", True)]).mapped("provider_id")
        )
        provider = next(
            (
                candidate
                for candidate in providers
                if verify_stripe_signature(payload, signature, candidate.sudo().stripe_terminal_webhook_secret)
            ),
            request.env["payment.provider"],
        )
        if not provider:
            return request.make_response("invalid signature", status=400)

        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            return request.make_response("invalid payload", status=400)
        event_id = event.get("id")
        event_type = event.get("type")
        if not event_id or not event_type:
            return request.make_response("invalid event", status=400)

        Event = request.env["southern.stripe.terminal.event"].sudo()
        existing = Event.search([("stripe_event_id", "=", event_id)], limit=1)
        if existing and existing.processed:
            return request.make_response("ok", status=200)

        event_object = (event.get("data") or {}).get("object") or {}
        payment_intent_id = None
        if event_type.startswith("payment_intent."):
            payment_intent_id = event_object.get("id")
        elif event_type.startswith("terminal.reader.action_"):
            action = event_object.get("action") or {}
            payment_intent_id = (action.get("process_payment_intent") or {}).get("payment_intent")

        terminal_payment = (
            request.env["southern.stripe.terminal.payment"]
            .sudo()
            .search(
                [("payment_intent_id", "=", payment_intent_id), ("provider_id", "=", provider.id)],
                limit=1,
            )
        )
        event_record = existing or Event.create(
            {
                "stripe_event_id": event_id,
                "event_type": event_type,
                "payment_id": terminal_payment.id or False,
            }
        )
        if terminal_payment and event_type in (
            "payment_intent.succeeded",
            "payment_intent.canceled",
            "terminal.reader.action_succeeded",
            "terminal.reader.action_failed",
        ):
            try:
                if event_type == "terminal.reader.action_failed":
                    action = event_object.get("action") or {}
                    terminal_payment.write(
                        {
                            "state": "failed",
                            "reader_action_status": "failed",
                            "stripe_failure_code": action.get("failure_code"),
                            "stripe_failure_message": action.get("failure_message"),
                        }
                    )
                terminal_payment._stripe_refresh_and_finalize()
                event_record.write({"processed": True, "processing_note": "Payment state refreshed from Stripe."})
            except Exception:
                _logger.exception("Stripe Terminal event %s requires retry", event_id)
                event_record.write(
                    {"processing_note": "Processing failed; operator refresh or webhook retry required."}
                )
                return request.make_response("retry", status=500)
        else:
            event_record.write({"processed": True, "processing_note": "No matching active terminal payment action."})
        return request.make_response("ok", status=200)

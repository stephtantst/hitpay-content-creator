"""
Insert pre-written X + Threads posts for Aug 20–31 2026 directly into the database.
Bypasses the Anthropic API entirely — content is final-approved copy.

Run from the project root with DATABASE_URL in the environment:
  railway run .venv/bin/python scripts/insert_aug_posts.py
  — or —
  DATABASE_URL="..." .venv/bin/python scripts/insert_aug_posts.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.x_database import create_x_post, change_x_post_status as change_x_status
from src.threads_database import create_threads_post, change_threads_post_status as change_thr_status

EDITOR = "content-batch@hit-pay.com"
BRAND = "hitpay"

# 9am SGT = UTC+8 = 01:00 UTC
def sat(date_str):
    return date_str + "T01:00:00Z"


POSTS = [

    # ── Aug 20 (Wed) ─ merchant_story ─ SG ──────────────────────────────────
    {
        "date": "2026-08-20",
        "market": "SG",
        "content_type": "merchant_story",
        "x": (
            "Wei Ling's craft shop in Chinatown gets mostly tourists. Half had Alipay or WeChat Pay, "
            "but she couldn't take either. She set up Borderless QR last year. "
            "Now she just shows one Borderless QR code: "
            "https://hitpayapp.com/sg/hitpay-borderless-qr-payments"
        ),
        "threads": (
            "We spoke with Wei Ling, who runs a heritage craft shop in Chinatown. Most of her customers are tourists. "
            "Before Borderless QR: she had PayNow and cash. About half her walk-ins had Alipay or WeChat Pay "
            "and couldn't pay. She was watching sales leave a few times a day."
            "\n\n---\n\n"
            "She's on HitPay's Borderless QR now. One code, all the major wallets — Alipay, WeChat Pay, and the rest. "
            "Nothing changes on her side. Tourists scan and go."
            "\n\n---\n\n"
            "She doesn't ask how people want to pay anymore. She just shows the code. "
            "If you're getting international shoppers, this might be helpful: "
            "https://hitpayapp.com/blog/cross-border-qr-acceptance-on-hitpay-terminals-serving-tourists-from-thailand-indonesia-and-beyond"
        ),
    },

    # ── Aug 21 (Thu) ─ product_focus ─ MY ───────────────────────────────────
    {
        "date": "2026-08-21",
        "market": "MY",
        "content_type": "product_focus",
        "x": (
            "Most Malaysian SMEs still chase monthly payments manually — calls, reminders, awkward follow-ups. "
            "HitPay recurring payment links fix that: set the amount, set the schedule, send once. "
            "Payments come in without you needing to follow-up: "
            "https://hitpayapp.com/blog/recurring-billing-my"
        ),
        "threads": (
            "We spoke with a few tuition centre owners in the Klang Valley who all said the same thing: "
            "monthly fee collection was the part of the job they liked least. "
            "Some were still calling parents, or chasing with WhatsApp messages every month."
            "\n\n---\n\n"
            "The tuition centre switched to HitPay recurring payment links. You set the amount and the schedule once, "
            "send the link to each parent, and that's it. The system handles reminders and collection automatically."
            "\n\n---\n\n"
            "One centre told us they got their first full collection month without a single follow-up call. "
            "If you're still doing this manually, why? :) There's a cleaner way: "
            "https://hitpayapp.com/blog/recurring-billing-my"
        ),
    },

    # ── Aug 22 (Fri) ─ thought_leadership ─ PH ──────────────────────────────
    {
        "date": "2026-08-22",
        "market": "PH",
        "content_type": "thought_leadership",
        "x": (
            "The most common reason Filipino shoppers abandon a transaction: no GCash, no QR Ph. "
            "These shops might have good product, but they're just hard to pay at. "
            "If you run a business, which customers are you quietly turning away?: "
            "https://hitpayapp.com/blog/cashless-payment-methods-philippines"
        ),
        "threads": (
            "When a small business in the Philippines loses a sale, it's usually not about the product. "
            "The customer was ready to pay; they just couldn't. "
            "GCash accounts for a large share of everyday consumer transactions. "
            "If you're not set up for it, you're not declining the payment. You're declining the customer. "
            "Read this to learn what payment methods matter in your market: "
            "https://hitpayapp.com/blog/cashless-payment-methods-philippines"
        ),
    },

    # ── Aug 23 (Sat) ─ thought_leadership ─ SG ──────────────────────────────
    {
        "date": "2026-08-23",
        "market": "SG",
        "content_type": "thought_leadership",
        "x": (
            "Many Singapore SMEs have a cash flow problem that's really an invoicing problem. "
            "The money is owed and the client will pay, but the invoice went out late. "
            "Here's how to fix the part you can actually control: "
            "https://hitpayapp.com/blog/invoice-payment"
        ),
        "threads": (
            "A pattern we see often with Singapore service businesses: the client is happy, the work is done, "
            "the payment isn't in dispute. But it's 30 days past due because no one followed up. "
            "Not a bad client. Just: invoice sent late, no auto-reminder, someone forgot. "
            "The money was always there; the system just didn't go get it. "
            "If that sounds familiar, read this: "
            "https://hitpayapp.com/blog/invoice-payment"
        ),
    },

    # ── Aug 24 (Sun) ─ product_focus ─ PH ───────────────────────────────────
    {
        "date": "2026-08-24",
        "market": "PH",
        "content_type": "product_focus",
        "x": (
            "GCash is how most Filipinos pay for everyday things. "
            "If your business doesn't accept it, you're cutting off customers "
            "who don't carry cash and won't use a card. "
            "If you're not set up yet, here's how to get started: "
            "https://hitpayapp.com/blog/gcash-hitpay"
        ),
        "threads": (
            "We were speaking with a bakery owner in Davao City who had a good regular customer base, "
            "mostly OFW families in the neighbourhood. She noticed they were starting to pay everything by GCash: "
            "groceries, bills, even the market. Her bakery was still cash only."
            "\n\n---\n\n"
            "She set up GCash QR through HitPay. No hardware needed. One QR code on the counter. "
            "The same families started coming in more often. Fewer \"I'll come back later\" moments."
            "\n\n---\n\n"
            "It wasn't a big change from her end, but it removed the friction for the people who mattered most. "
            "If GCash is how your community pays, here's how to get set up: "
            "https://hitpayapp.com/blog/gcash-hitpay"
        ),
    },

    # ── Aug 25 (Mon) ─ product_focus ─ SG ───────────────────────────────────
    {
        "date": "2026-08-25",
        "market": "SG",
        "content_type": "product_focus",
        "x": (
            "Most Singapore customers expect PayNow. "
            "Most small business sites still send manual bank transfer instructions. "
            "HitPay payment links let you accept PayNow, cards, and GrabPay in one URL. "
            "No manual follow-up, no reminders. Here's how it works: "
            "https://hitpayapp.com/blog/paynow-payment-gateway-singapore"
        ),
        "threads": (
            "We spoke with a freelance designer in Singapore who was still collecting payment the old way: "
            "sending her UEN by WhatsApp, waiting for a screenshot, manually checking her bank. "
            "Three active clients, reconciling by memory."
            "\n\n---\n\n"
            "She switched to HitPay payment links. Each client gets a link with the amount preset. "
            "PayNow, card, GrabPay; they pick what works. She gets a notification when each one pays."
            "\n\n---\n\n"
            "She told us she stopped checking her bank three times a day. "
            "Everything comes through one dashboard now. "
            "If you're still doing manual bank transfers, why? :) There's a better way: "
            "https://hitpayapp.com/blog/paynow-payment-gateway-singapore"
        ),
    },

    # ── Aug 26 (Tue) ─ thought_leadership ─ MY ──────────────────────────────
    {
        "date": "2026-08-26",
        "market": "MY",
        "content_type": "thought_leadership",
        "x": (
            "DuitNow QR has changed what Malaysian customers expect. "
            "Younger shoppers reach for their phone before looking for cash. "
            "Shops still on cash-only are losing transactions they'll never know they lost. "
            "Here's how to get set up: "
            "https://hitpayapp.com/blog/how-to-set-up-duitnow-qr-malaysia-business"
        ),
        "threads": (
            "Something shifting in Malaysia's payments landscape: DuitNow QR has moved from a nice-to-have "
            "to an expectation. Younger customers reach for their phones before looking for cash. "
            "Shops that aren't set up for it aren't losing arguments about payment; they're just losing sales. "
            "If you're not sure whether your setup is current, this is for you: "
            "https://hitpayapp.com/blog/how-to-set-up-duitnow-qr-malaysia-business"
        ),
    },

    # ── Aug 27 (Wed) ─ merchant_story ─ MY ──────────────────────────────────
    {
        "date": "2026-08-27",
        "market": "MY",
        "content_type": "merchant_story",
        "x": (
            "Faizal runs a night market stall in Kota Kinabalu. "
            "Half his customers pay by e-wallet, half use cash. "
            "He was turning away the e-wallet half every night until he put up DuitNow QR. "
            "Now both pay without issue. If you're in the same boat, this might be helpful: "
            "https://hitpayapp.com/blog/popular-payment-methods-malaysia"
        ),
        "threads": (
            "We were at Faizal's stall at the Kota Kinabalu night market on a Friday. "
            "It's busy. Food, lights, people moving fast. "
            "He's taking orders and making change at the same time. "
            "A customer reaches for their phone before reaching for their wallet."
            "\n\n---\n\n"
            "He taps his DuitNow QR, laminated and propped against the display. "
            "The customer scans. Done. Faizal calls the next order before the notification plays."
            "\n\n---\n\n"
            "He said it used to be awkward. \"Cash only\" meant some customers went to the stall next door. "
            "Now it just works in both directions. "
            "If you're a night market vendor in Malaysia, this one's for you: "
            "https://hitpayapp.com/blog/popular-payment-methods-malaysia"
        ),
    },

    # ── Aug 28 (Thu) ─ product_focus ─ PH ───────────────────────────────────
    {
        "date": "2026-08-28",
        "market": "PH",
        "content_type": "product_focus",
        "x": (
            "QR Ph is becoming the default way Filipinos pay at physical stores. "
            "GCash, Maya, and most bank apps; one scan. "
            "HitPay generates a single QR Ph code that works with all of them. "
            "Here's how to get started: "
            "https://hitpayapp.com/blog/qrph-payment"
        ),
        "threads": (
            "We spoke with a coffee shop owner in Tagaytay who gets a lot of weekend day-trippers from Manila. "
            "Most were paying by GCash or Maya; they weren't carrying much cash after the drive up."
            "\n\n---\n\n"
            "She set up QR Ph through HitPay. One QR code handles GCash, Maya, and most Philippine bank apps. "
            "She put it on the counter and at the cashier station."
            "\n\n---\n\n"
            "Weekend sales picked up. Not because the coffee got better, "
            "but because people stopped hesitating at the counter. "
            "If you're looking to accept QR Ph without the complexity, this is for you: "
            "https://hitpayapp.com/blog/qrph-payment"
        ),
    },

    # ── Aug 29 (Fri) ─ thought_leadership ─ SG ──────────────────────────────
    {
        "date": "2026-08-29",
        "market": "SG",
        "content_type": "thought_leadership",
        "x": (
            "The most common way Singapore SMEs lose online sales: "
            "checkout redirects to a page customers don't recognise. "
            "Product was fine. Price was fine. The payment step lost them. "
            "If cart abandonment is high, here's how to fix it: "
            "https://hitpayapp.com/blog/accept-online-payments-singapore"
        ),
        "threads": (
            "A pattern worth paying attention to if you run an online store in Singapore: "
            "customers are comfortable with PayNow and GrabPay. "
            "They're less comfortable being redirected to an unfamiliar gateway mid-checkout. "
            "When cart abandonment is high, the instinct is to look at the product or the price. "
            "Often it's neither. "
            "Worth testing what your checkout actually looks like to a first-time buyer. "
            "Here's how to start: "
            "https://hitpayapp.com/blog/accept-online-payments-singapore"
        ),
    },

    # ── Aug 30 (Sat) ─ thought_leadership ─ SG ──────────────────────────────
    {
        "date": "2026-08-30",
        "market": "SG",
        "content_type": "thought_leadership",
        "x": (
            "BNPL isn't just for big purchases. "
            "Plenty of Singapore customers use Atome or GrabPay Later for everyday buys they could pay upfront. "
            "If your checkout doesn't offer it, some of those customers are quietly choosing a competitor that does. "
            "Worth understanding how it works: "
            "https://hitpayapp.com/blog/bnpl-singapore-buy-now-pay-later"
        ),
        "threads": (
            "Something interesting about BNPL in Singapore: it's not primarily being used for large purchases. "
            "People are using Atome and GrabPay Later on food, clothing, and services; "
            "things they could easily pay upfront. It's not about affordability. It's about preference. "
            "For merchants, offering BNPL at checkout doesn't just help customers who need instalments. "
            "It also captures customers who simply prefer them. "
            "Here's what that looks like in practice: "
            "https://hitpayapp.com/blog/bnpl-singapore-buy-now-pay-later"
        ),
    },

    # ── Aug 31 (Sun) ─ product_focus ─ MY ───────────────────────────────────
    {
        "date": "2026-08-31",
        "market": "MY",
        "content_type": "product_focus",
        "x": (
            "Malaysian customers are increasingly tapping to pay: contactless card, no PIN. "
            "HitPay's card reader accepts Visa, Mastercard, and AmEx tap payments. "
            "No cash float, no change. "
            "If you're thinking about upgrading your terminal, this is for you: "
            "https://hitpayapp.com/blog/tap-to-pay-malaysia"
        ),
        "threads": (
            "We spoke with a boutique fashion store owner in Petaling Jaya. "
            "She noticed last year that more customers were getting frustrated when she asked them to enter their PIN. "
            "They wanted to tap and go. Her card terminal at the time only supported chip."
            "\n\n---\n\n"
            "She switched to a HitPay card reader that accepts contactless payments: "
            "tap for Visa, Mastercard, AmEx. The checkout queue got shorter. Fewer customers digging for cash."
            "\n\n---\n\n"
            "The shift felt small at first. But she stopped keeping a cash float at the register, "
            "and that alone saved her time every day. "
            "If you're thinking about upgrading your terminal, this is for you: "
            "https://hitpayapp.com/blog/tap-to-pay-malaysia"
        ),
    },

]


def main():
    print(f"Inserting {len(POSTS)} days of posts (X + Threads each)...\n")

    weekday_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for p in POSTS:
        scheduled = sat(p["date"])
        from datetime import date as _d
        wd = _d.fromisoformat(p["date"]).weekday()

        x_id = None
        thr_id = None

        try:
            x_id = create_x_post(
                content=p["x"],
                market=p["market"],
                scheduled_at=scheduled,
                editor_email=EDITOR,
                brand=BRAND,
            )
            change_x_status(x_id, "scheduled", scheduled_at=scheduled)
        except Exception as e:
            print(f"  X post failed for {p['date']}: {e}")

        try:
            thr_id = create_threads_post(
                content=p["threads"],
                market=p["market"],
                scheduled_at=scheduled,
                editor_email=EDITOR,
                brand=BRAND,
            )
            change_thr_status(thr_id, "scheduled", scheduled_at=scheduled)
        except Exception as e:
            print(f"  Threads post failed for {p['date']}: {e}")

        tag = weekday_name[wd]
        status = "OK" if x_id and thr_id else "PARTIAL" if (x_id or thr_id) else "FAILED"
        print(f"  [{status}] {p['date']} ({tag})  {p['content_type']:<20}  X={x_id}  Threads={thr_id}")

    print("\nDone. Run the app and check X + Threads tabs to review and edit posts.")


if __name__ == "__main__":
    main()

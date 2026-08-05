# Southern Customer Portal

Adds customer portal pages for Southern Equipment customers:

- `/my/membership` lets a customer submit the Southern Equipment membership agreement from the portal.
- `/my/repair-orders` shows outstanding repair orders for the logged-in customer's commercial partner.
- `/my/outstanding-invoices` shows posted customer invoices with an open balance.
- `/my/home` highlights customer account status and suppresses unrelated portal cards such as projects, tasks, timesheets, vendor documents, and vendor orders.

Membership applications are stored in **Southern Membership > Applications**. The module records the PDF terms as structured Odoo logic: 30-day trial, $25 monthly fee, 5% parts/service discount, $2,500 standard house-credit limit, separate approval flag for higher credit requests, member acceptance, and billing authorization. Raw card numbers, expiration dates, and CVV are intentionally not stored.

Deploy this addon to the Odoo.sh custom addons repo/branch, update the app list, then install **Southern Customer Portal**.

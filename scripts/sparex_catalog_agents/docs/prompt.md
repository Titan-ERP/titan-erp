# Catalog agent contract

Catalog agents reason only over the compact JSON snapshot prepared by Odoo.
They must not invent or retrieve additional facts. Deterministic code owns SKU
normalization, Odoo matching, supplier-cost presence, sales-price presence,
Sparex URL validation, image presence, hidden/public state, and readiness.

Every agent must call its one assigned read-only function tool before returning
a decision. The tool reads the snapshot from SDK run context, so the model
cannot substitute arbitrary arguments. No hosted web, file, MCP, shell,
computer-use, or write-capable tool is exposed.

The four business requirements for release readiness are:

- positive existing matching Sparex supplier cost;
- positive existing Odoo sales price;
- exact linked HTTPS Sparex URL for the same SKU;
- image presence.

An agent may recommend an action. It cannot create products, change costs or
prices, alter standard cost, write content, or change publication flags.

# AWS automation credential migration

The product worker must not run with AWS account-root credentials. Create a
dedicated IAM role for the execution environment and render
`cloud/aws/product-automation-role-policy.template.json` with the production
account, region, SSM document, instance, bucket, and artifact prefix.

Required controls:

1. Prefer an EC2 instance profile, GitHub OIDC role, or AWS IAM Identity Center
   session. Do not create a long-lived access key unless no role-based option is
   available.
2. Attach only the rendered product-automation policy. Do not attach
   `AdministratorAccess`, IAM write permissions, or wildcard S3 object access.
3. Require MFA for human operators and retain a separate break-glass role.
4. Verify with `aws sts get-caller-identity` that the caller ARN is the named
   role and is not `root` before enabling a write window.
5. Run the worker first with `ODOO_WRITE_ENABLED` unset. Confirm the Odoo run
   ledger, 2 GB disk floor, batch bound, idempotency key, and S3 prefix.
6. Rotate or delete the former automation credential only after the role-based
   dry run succeeds. Never commit credential material or paste it into logs.

The repository intentionally does not create or rotate IAM credentials. That
security-sensitive cutover requires an authenticated AWS administrator and a
recorded rollback owner.

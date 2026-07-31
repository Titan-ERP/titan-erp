from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    southern_openai_api_key = fields.Char(
        string="OpenAI API Key",
        config_parameter="southern_service_operations.openai_api_key",
        groups="base.group_system",
    )
    southern_openai_model = fields.Char(
        string="OpenAI Model",
        default="gpt-5.6-sol",
        config_parameter="southern_service_operations.openai_model",
        groups="base.group_system",
    )
    southern_openai_vector_store_id = fields.Char(
        string="Equipment Manual Vector Store",
        config_parameter="southern_service_operations.openai_vector_store_id",
        groups="base.group_system",
    )

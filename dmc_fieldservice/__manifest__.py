# -*- coding: utf-8 -*-
{
    'name': "DMC Field Service",

    'summary': "Adds equipment, serial number, and run hours fields to Field Service tasks",

    'description': """
Extends the Field Service task form with equipment tracking fields.

Features
--------
- Equipment name field on FSM tasks
- Serial number field on FSM tasks
- Equipment run hours field on FSM tasks
- All fields tracked in the chatter
""",

    'author': "DMC Strategict It",
    'website': "https://www.dmcstrategicit.com",

    'version': '19.0.1.0.0',

    'application': False,
    'installable': True,

    'license': 'OPL-1',

    'depends': ['industry_fsm', 'industry_fsm_sale'],

    'data': [
        'views/project_task_views.xml',
    ],
}

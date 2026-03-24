# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

import logging
import datetime

from odoo.http import Controller, request, route
from odoo import _

_log = logging.getLogger(__name__)

COLOR_PLAN = '#1B13BF'
COLOR_CONTRACT = ['#22DEBD', '#FF6276', '#EAD922', '#443CE3']
COLOR_REGITRATION = ['#2ECDDC', '#FFD471', '#685AFD', '#71FFCE', '#F6F6F6', '#5C9BEF']


class Dashboard(Controller):

    def execute_cr(self, query, label, color, border_color=False, border_width=1, legend=False):
        labels = [0] if label == 'Dealer Application' else []
        data = [0] if label == 'Dealer Application' else []
        result = []

        try:
            _cr = request.env.cr
            _cr.execute(query)
            result_cr = _cr.fetchall()

            for _x in result_cr:
                labels.append(str(_x[0]).capitalize())
                data.append(_x[1])

            result = {
                'labels': labels,
                'datasets': [{
                    'axis': 'y',
                    'label': label,
                    'data': data,
                    'fill': False,
                    'order': 1,
                    'backgroundColor': color,
                    'borderColor': border_color,
                    'borderWidth': border_width,
                    'hoverBackgroundColor': color,
                    'hoverBorderColor': border_color,
                    'barPercentage': 0.9,
                    'barThickness': 60,
                    'borderRadius': 'bottomRight',
                    'pointStyle': 'star',
                    'categoryPercentage': 0.5,
                    'maxBarThickness': 20,
                    'minBarLength': 2,
                }]
            }

            if legend:
                total = sum(result.get('datasets')[0]['data'])
                legend_text = ['{}% {}'.format(
                    round((float(t[1])*100/total), 2), t[0]) for t in result_cr]
                result['legend_text'] = legend_text

        except Exception as e:
            pass
        return result

    def dealer_application_conversion(self):


        total_application = request.env['dealership.application'].search_count([])
        total_conversion = request.env['dealership.application'].search_count([('state', '=', 'done')])
        response = {
            'application': total_application,
            'conversion': total_conversion
        }
        return response

    @route(['/dashboard/home'], type='jsonrpc', auth="user", website=True)
    def dashboard_home(self, **kw):
        contract_state_query = '''
            SELECT
                state,
                COUNT(*) AS total
            FROM
                dealership_contract
            GROUP BY
                state;
        '''

        plan_stat_qyuery = '''
            SELECT
                p.name AS p_name,
                COUNT(*) AS users
            FROM
                dealership_application d
            INNER JOIN
                dealership_plan p
            ON
                d.plan_id = p.id
            WHERE
                d.state = 'done'
            GROUP BY
                p.name;
        '''

        contract_state = self.execute_cr(
            contract_state_query, 'Contract State', COLOR_CONTRACT, COLOR_CONTRACT, legend=True)
        plan_stat = self.execute_cr(plan_stat_qyuery, 'Active Users', COLOR_PLAN, COLOR_PLAN, legend=True)
        dealer_conversion = self.dealer_application_conversion()
        side_lead_stat = self.leads_stat()
        top_header_stats = self.top_header_stats(
            dealer_conversion, side_lead_stat)
        location = {
            'map_key': request.env['ir.config_parameter'].sudo().get_param('dealership_management.google_map_api_key'),
            'countries': request.env['res.country'].sudo().search([]).read(['id', 'name'])
        }

        response = {
            'side_lead_stat': side_lead_stat,
            'top_header_stats': top_header_stats,
            'contract_state': contract_state,
            'plan_stat': plan_stat,
            'total_registration_stat': self.total_registration_stat(),
            'top_products_stat': self.top_products_stat(),
            'total_sale_stat': self.total_sale_stat(),
            'total_leads_stat': self.total_leads_stat(),
            'dealer_conversion': dealer_conversion,
            'location': location
        }
        return response

    @route(['/dashboard/update_data'], type='jsonrpc', auth="user", website=True)
    def dashboard_update_data(self, call, **kw):
        response = False
        try:
            method_to_call = getattr(self, call)
            response = method_to_call(**kw)
        except Exception as e:
            pass
        return response

    def total_registration_stat(self, time_period=1):
        # time_period int: is the month you want get status
        today = datetime.date.today()
        time = today - datetime.timedelta(days=30*time_period)
        query = '''
            SELECT
                state,
                COUNT(*) AS total
            FROM
                dealership_application
            WHERE
                create_date > '{}'
            AND
                active = true
            GROUP BY
                state;
        '''.format(time)

        application = self.execute_cr(
            query, 'Dealer Application', COLOR_REGITRATION, '#FFFFFF', 10, True)
        return application

    def top_products_stat(self, time_period=1, limit=5):
        today = datetime.date.today()
        time = today - datetime.timedelta(days=30*time_period)
        query = '''
            SELECT
                ol.name AS p_name,
                SUM(ol.product_uom_qty) AS total
            FROM
                sale_order_line ol
            LEFT JOIN
                product_product p
            ON
                ol.product_id = p.id
            LEFT JOIN
                product_template pt
            ON
                p.product_tmpl_id = pt.id
            WHERE
                pt.is_published = TRUE
            AND
                ol.state != 'draft'
            AND
                ol.create_date > '{}'
            GROUP BY
                ol.name
            ORDER BY
                total DESC
            LIMIT {};
        '''.format(time, limit)

        products = self.execute_cr(query, 'Product Sale', '#5C9BEF', '#5C9BEF')
        return products

    def top_dealer_stat(self, time_period=1, limit=5):
        today = datetime.date.today()
        time = today - datetime.timedelta(days=30*time_period)
        query = '''
            SELECT
                name,
                app_avg_rating
            FROM
                dealership_application
            WHERE
                create_date > '{}'
            AND
                app_avg_rating != 0
            ORDER BY
                app_avg_rating DESC
            LIMIT {}
        '''.format(time, limit)

        dealer = self.execute_cr(query, 'Dealer Rating', '#5C9BEF', '#5C9BEF')
        return dealer

    def total_sale_stat(self, time_period=1, ):
        today = datetime.date.today()
        time = today - datetime.timedelta(days=30*time_period)
        query = '''
            SELECT
                p.name AS user_name,
                SUM(o.amount_total) AS o_total
            FROM
                sale_order o
            LEFT JOIN
                res_partner p
            ON
                o.partner_id = p.id
            WHERE
                o.state != 'draft'
            AND
                o.amount_total > 0
            AND
                o.create_date > '{}'
            GROUP BY
                p.name
            ORDER BY
                o_total ASC;
        '''.format(time)
        sale = self.execute_cr(query, 'Total Sale', '#1B13BF', '5C9BEF')
        return sale

    def total_leads_stat(self):
        query = '''
            SELECT
                cs.name AS s_name,
                COUNT(*)
            FROM
                crm_lead c
            LEFT JOIN
                crm_stage cs
            ON
                c.stage_id = cs.id
            WHERE
                c.active = true
            GROUP BY
                cs.name;
        '''

        total_lead_stat = self.execute_cr(
            query, 'Total Leads', '#2959E2', '#2959E2')
        labels = [label.split("'")[3].upper() for label in total_lead_stat.get('labels')]
        total_lead_stat.update({'labels': labels})
        return total_lead_stat

    def leads_stat(self):
        leads = request.env['crm.lead']
        total = leads.search_count([])
        unassigned = leads.search_count([('user_id', '=', False)])
        assigned = leads.search_count([('user_id', '!=', False)])

        return {
            'total': total,
            'unassigned': unassigned,
            'assigned': assigned
        }

    def top_header_stats(self, other_state={}, leads={}):
        result = []
        today = datetime.date.today()
        start_day = today.replace(day=1)

        # *********************************************************
        # *********************************************************
        # ***   Using ORM at place of sql query,bcz its getting ***
        # ***   sale_report model error at the time of fetching ***
        # *********************************************************
        # *********************************************************

        sum_of_price_subtotal = sum(request.env['sale.report'].search([
            ('state', 'in', ['done', 'sale']),
            ('date', '>=', start_day),
            ('company_id', '=', request.env.company.id)
        ]).mapped('price_subtotal'))

        month_sale = "{} {}".format(round(sum_of_price_subtotal/1000, 2), 'K')

        total_product = request.env['product.product'].search_count(
            [('is_published', '=', True)])

        result.extend([{
            'name': 'Sales of this month',
            'id': 'sale_report',
            'value': month_sale or '0',
            'img': 'icon-sales.png',
            'domain': [('state', 'in', ['sale', 'done']), ('date', '>=', start_day)],
            'model': 'sale.report',
            'views': [[False, 'graph']]
        },
            {
            'name': 'Total Products',
            'id': 'total_products',
            'value': total_product or '0',
            'img': 'icon-products.png',
            'domain': [('is_published', '=', True)],
            'model': 'product.product',
            'views': [[False, 'kanban'], [False, 'form']]
        },
            {
            'name': 'Total Leads',
            'id': 'total_leads',
            'value': leads.get('total') or '0',
            'img': 'icon-leads.png',
            'domain': [('active', '=', True)],
            'model': 'crm.lead',
            'views': [[False, 'list'], [False, 'form']]
        }])

        if other_state:
            result.extend([{
                'name': 'Total Registration',
                'id': 'total_registration',
                'value': other_state.get('application'),
                'img': 'icon-register.png',
                'domain': [],
                'model': 'dealership.application',
                'views': [[False, 'list'], [False, 'kanban'], [False, 'form']]
            },
                {
                'name': 'Total Dealers',
                'id': 'total_dealers',
                'value': other_state.get('conversion'),
                'img': 'total-dealers.png',
                'domain': [('state', '=', 'done')],
                'model': 'dealership.application',
                'views': [[False, 'list'], [False, 'kanban'], [False, 'form']]
            }])

        return result

    def dealer_location_stat(self, country_id=0, state_id=0):
        name = ''
        query = '''
            SELECT
                c.name AS c_name,
                COUNT(*) AS total
            FROM
                dealership_application dl
            LEFT JOIN
                res_country c
            ON
                dl.country_id = c.id
            WHERE
                dl.state = 'done'
            GROUP BY
                c.name
        '''

        if country_id:
            query = '''
                SELECT
                    c.name AS c_name,
                    COUNT(*) AS total
                FROM
                    dealership_application dl
                LEFT JOIN
                    res_country_state c
                ON
                    dl.state_id = c.id
                WHERE
                    dl.state = 'done'
                AND
                    dl.country_id = {}
                GROUP BY
                    c.name
            '''.format(country_id)
            name = request.env['res.country'].browse(country_id)['name']

        if state_id:
            query = '''
                SELECT
                    dl.zip AS s_zip,
                    COUNT(*) AS total
                FROM
                    dealership_application dl
                LEFT JOIN
                    res_country_state c
                ON
                    dl.state_id = c.id
                WHERE
                    dl.state = 'done'
                AND
                    dl.state_id = {}
                GROUP BY
                    dl.zip
            '''.format(state_id)
            name += ' ' + \
                request.env['res.country.state'].browse(state_id)['name']

        _cr = request.env.cr
        _cr.execute(query)
        applications = _cr.fetchall()

        return {'applications': applications, 'name': name}

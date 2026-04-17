# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

import logging
from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.fields import Domain
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.addons.account.controllers.portal import PortalAccount

_log = logging.getLogger(__name__)

DEFAULT_LIMIT = 20

class PortalCustomerInvoices(PortalAccount):

    @http.route()
    def portal_my_invoices(self, page=1, date_begin=None, date_end=None, sortby=None, filterby=None, **kw):
        response = super(PortalCustomerInvoices, self).portal_my_invoices(page=page, date_begin=date_begin, date_end=date_end, sortby=sortby, filterby=filterby, **kw)
        application_id = request.env.user.sudo().application_id
        if kw.get('plan_invoice') and application_id:
            AccountInvoice = request.env['account.move']
            contract = application_id.get_related_contract()
            invoice_ids = []
            for line in contract.contract_line:
                invoice_ids += line.sale_order_id.invoice_ids.ids
            domain = [('id', 'in', invoice_ids)]
            invoice_count = AccountInvoice.search_count(domain)
            # pager
            pager = portal_pager(
                url="/my/invoices?plan_invoice=True",
                url_args={'date_begin': date_begin, 'date_end': date_end, 'sortby': sortby},
                total=invoice_count,
                page=page,
                step=self._items_per_page
            )
            invoices = AccountInvoice.search(domain, order='id desc', limit=self._items_per_page, offset=pager['offset'])
            request.session['my_invoices_history'] = invoices.ids[:100]
            response.qcontext.update({
                'invoices': invoices,
                'pager': pager,
                'default_url': '/my/invoices?plan_invoice=True',
            })
        return response


class ApplicationDashboard(WebsiteSale):

    def render_template(self, name, param):
        t_name = 'dealership_management.{}'.format(name)
        render = request.env['ir.ui.view']._render_template(t_name, param)
        return render


    def pager(self, count, limit, offset=0, url_arg=''):
        if count:
            next = True if count > (offset+limit) else False
            prev = True if offset else False

            if offset+1 == offset+limit:
                index = offset+1
            else:
                max = offset+limit if count > (offset+limit) else count
                index = '{}-{}'.format(offset+1, max)

            response = {
                'limit': limit,
                'next': next,
                'prev': prev,
                'count': count,
                'url': request.httprequest.path + url_arg,
                'index':  index
            }

            return response
        return False


    def get_product_domain(self, is_all_products=False, search=False, **kw):
        domain = [request.website.sale_product_domain()]
        if search:
            for srch in search.split(" "):
                subdomains = [
                    [('name', 'ilike', srch)],
                    [('product_variant_ids.default_code', 'ilike', srch)]
                ]
                if is_all_products:
                    subdomains = [
                        [('name', 'ilike', srch)],
                        [('default_code', 'ilike', srch)]
                    ]
                domain.append(Domain.OR(subdomains))
        domain.append([('website_published', '=', True)])
        return Domain.AND(domain)

    @http.route(['/application/dashboard'], type='http', auth="user", website=True)
    def application_dashboard(self,**kw):
        application = request.env.user.sudo().application_id
        if application and application.state=='done':
            contract = application.get_related_contract()
            params = {
                'application': application,
                'active_contract_line': contract.get_active_contract_line(),
                'contract': contract
            }
            return request.render('dealership_management.application_dashboard', params)
        else:
            raise NotFound()



    @http.route(['/application/profile'], type='jsonrpc', auth="user", website=True)
    def application_profile(self,**kw):
        application = request.env.user.sudo().application_id
        return self.render_template('dashboard_user_info', {'application': application})


    @http.route('/application/profile/change', type='jsonrpc', auth="user", website=True)
    def my_profile_save(self, **kw):
        res_user = request.env['res.users'].sudo().search([('id','=',request.env.user.id)])
        datas = kw.get('datas')
        if res_user and datas:
            datas = datas.split(',')[1].strip()
            res_user.image_1920 = datas
        return { 'result': True }



    @http.route(['/application/all_products'], type='jsonrpc', auth="user", website=True)
    def application_all_products(self, search='', limit=0, offset=0, **kw):
        is_all_products = request.httprequest.args.get('app')
        limit = limit and limit or DEFAULT_LIMIT
        application_id = request.env.user.application_id
        context = {'pricelist': request.pricelist}
        url_arg = ''
        domain = self.get_product_domain(is_all_products=is_all_products, search=search, **kw)
        if is_all_products:
            url_arg = "?app=1"
            domain = Domain.AND([[('id', 'in', application_id.stock_available.mapped('product_id.id'))], domain])
            model = 'product.product'
        else:
            model = 'product.template'
            context['app_products_ids'] = application_id.stock_available.mapped('product_id.product_tmpl_id.id')
        seach_count = request.env[model].search_count(domain)
        pager = self.pager(seach_count, limit, offset, url_arg)
        products = request.env[model].search(domain, limit=limit, offset=offset)
        context['products'] = products
        context['pager'] = pager
        context['search'] = search
        context['is_all_products'] = not is_all_products
        return self.render_template('dashboard_all_products', context)


    @http.route(['/application/add_product'], type='jsonrpc', auth="user", website=True)
    def application_add_product(self, product_id=False, product_temp=False, **kw):
        if kw.get('params').get('product_temp'):
            product_temp = kw.get('params').get('product_temp')

        if kw.get('params').get('product_id'):
            product_id = kw.get('params').get('product_id')

        if product_temp:
            product_temp = request.env['product.template'].sudo().browse(product_temp)
            return self.render_template('product_variant_details', {'product': product_temp})

        if product_id:
            application_id = request.env.user.sudo().application_id
            sale_order = request.cart or request.website._create_cart()

            if application_id:
                response = sale_order.with_context(skip_cart_verification=True)._cart_add(
                    product_id=product_id,
                    quantity=1
                )
                line_id = request.env['sale.order.line'].sudo().browse(response['line_id'])
                line_id.is_dealer_application = True
                response['cart_quantity'] = sale_order.cart_quantity
                return response
        return False

    @http.route(['/application/sale_order'], type='jsonrpc', auth="user", website=True)
    def application_sale_order(self, limit=0, offset=0, **kw):
        partner = request.env.user.partner_id
        limit = limit and limit or DEFAULT_LIMIT
        param = {}

        domain = [('message_partner_ids', 'child_of', [partner.commercial_partner_id.id])]
        seach_count = request.env['sale.order'].search_count(domain)
        pager = self.pager(seach_count, limit, offset)

        sale_order = request.env['sale.order'].search(domain, limit=limit, offset=offset)

        if sale_order:
            param = {'orders': sale_order, 'pager': pager}
        return self.render_template('dashboard_sale_orders', param)


    @http.route(['/application/lead'], type='jsonrpc', auth="user", website=True)
    def application_lead(self, limit=0, offset=0, **kw):
        user_id = request.env.user
        limit = limit and limit or DEFAULT_LIMIT
        param = {}
        seach_count = request.env['crm.lead'].sudo().search_count([('user_id', '=', user_id.id)])
        leads = request.env['crm.lead'].sudo().search([('user_id', '=', user_id.id)], limit=limit, offset=offset)
        pager = self.pager(seach_count, limit, offset)
        if leads:
            param = {'leads': leads, 'pager': pager}
        return self.render_template('application_leads', param)


    @http.route(['/application/contract'], type='jsonrpc', auth='user', website=True)
    def application_contract(self, **kw):
        application_id = request.env.user.sudo().application_id
        if application_id:
            return self.render_template('application_contract', {'contract': application_id.get_related_contract()})
        return False

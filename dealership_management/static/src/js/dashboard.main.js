/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { utils } from "@web/core/ui/ui_service";
import { rpc } from "@web/core/network/rpc";
import wSaleUtils from "@website_sale/js/website_sale_utils";

  publicWidget.registry.DealershipDashboard = publicWidget.Widget.extend({
    selector: ".dealership-dashboard",
    events: {
      'click .dashboard-hider': '_shrinkDashboard',
      'click .dashboard-side-nav .dnav-item': '_openDashboardView',
      'click .dashboard-all-products .change-view': '_toggleProductView',
      'click .dashboard-all-products .add-to-application, .dashboard-all-products .add-more-qty': '_addToApp',
      'click .dashboard-dynamic-pager button': '_setPagerParameter',
      'change .dashboard-dynamic-pager [name="loaded_element"]': '_setFetchParameter',
      'keyup .dashboard-dynamic-pager [name="loaded_element"]': '_setFetchParameterCall',
      'change .user-image input[type="file"]': '_changeUserImage',
      'click .dl_search_button': '_search_items'
    },
    init() {
      this._super(...arguments);
    },

    start: function () {
      $("#wrapwrap").addClass("dashboard-parent");
      this.$dashboard = this.$target.find('[render-dashboard-view="1"]');
      this.loader = this.$dashboard.find('#dealership_dashboard_loader');
      this.param = {};
      this._trigger_active_view();
      this.setPlanProgress();
    },

    _trigger_active_view: function () {
      var route = window.sessionStorage.getItem("dashboard_view") || '/application/profile';
      var ele = $(`[d_href="${route}"]`)
      this._render_main_view(ele , {search_box: false});
    },

    setPlanProgress: function () {
      var $ppc = $('.progress-pie-chart'),
        percent = parseInt($ppc.data('percent')) || 0,
        deg = 360*percent/100;
      if (percent > 50) {
        $ppc.addClass('gt-50');
      }
      $('.ppc-progress-fill').css('transform','rotate('+ deg +'deg)');
    },

    _changeUserImage: function (ev) {
      var self = this;
      var image = ev.currentTarget.files[0];
      var loader = $(ev.currentTarget).closest('label');
      loader.removeClass('fa-pencil').addClass('fa-spinner fa-spin').parents('div').addClass('active');
      var reader = new FileReader();
      reader.onload = function(e) {
        var datas = e.target.result;
        try {
          rpc('/application/profile/change',{
            params: {'datas': datas}
          }).then(function (res) {
            if (res.result) {
              $('.user-img').find('img').attr('src', datas);
            }
            loader.removeClass('fa-spinner fa-spin').addClass('fa-pencil').parents('div').removeClass('active');
          })
        } catch (e) {console.log(e);}
      };
      if (image) {
        reader.readAsDataURL(image);
      } else {
        loader.removeClass('fa-spinner fa-spin').addClass('fa-pencil').parents('div').removeClass('active');
      }
    },

    _shrinkDashboard: function (ev) {
      this.$target.find('.dashboard-side-nav').toggleClass('shrink');
    },

    _render_main_view: function (ele, filter) {
      if (filter.remove_search) {
        if (this.search_box) {
          this.search_box.val('');
        }
      }
      this._render(ele);
    },

    _openDashboardView: function (ev) {
      ev.preventDefault();
      this._render_main_view($(ev.currentTarget), {remove_search: true});
    },

    _call: function (route, params) {
      params = this._get_filter_records(params);
      return rpc(route,{params:params});
    },

    _get_filter_records: function (params) {
      // apply all the search filter here on any record
      if (this.search_box) {
        params.search = this.search_box.val() || false;
      } else {
        params.search = false;
      }
      return params
    },

    _render: function ($ele) {
      var route = $ele.attr('d_href');
      var self = this;
      var _is_product = $ele.attr('for') == 'product';

      self.$dashboard.append(self.loader);
      self.$target.find('.dnav-item').removeClass('active');
      $('.product-stock-info').popover('dispose');

      if (route){
        this._call(route, self.param).then(function (resposne) {
          if (_is_product) {
            resposne = self.setProductViewType($(resposne));
          }
          self.$dashboard.scrollTop(0);
          self.$dashboard.html(resposne);
          $ele.addClass('active');
          self.search_box = self.$dashboard.find('.dl_search_box');
          window.sessionStorage.setItem("dashboard_view", route);
          self.param = {};
          $('[data-toggle="tooltip"]').tooltip({delay: { "show": 50, "hide": 100 }});
  
          $('.product-stock-info').each(function () {
            var item = $(this);
            item.popover({
              html: true,
              content: function () {
                $('.product-stock-info').popover('hide');
                return item.closest('.total_product').find('#tip-stock').html();
              }
            })
          })
        });
      }
      else{
        location.reload();
      }
    },

    _toggleProductView: function (ev) {
      window.sessionStorage.setItem("product_view", ev.currentTarget.getAttribute('view_type'));
      this.setProductViewType(this.$target);
    },

    setProductViewType: function ($data) {
      var views = "list grid";
      var view_type = window.sessionStorage.getItem("product_view") || 'grid';

      $data.find('.product-container').removeClass(views).addClass(view_type);

      $data.find('.change-view').each(function () {
        $(this).removeClass('active');
        if ( this.getAttribute('view_type') == view_type ) {
          $(this).addClass('active');
        }
      });

      return $data;
    },

    _addToApp: function (ev) {
      ev.preventDefault();
      var ele = ev.currentTarget;
      var product_id = parseInt(ele.getAttribute('data_id')) || parseInt($(ele).closest('form').find('input.product_id').val());
      var is_temp = ele.getAttribute('single_variants') == 'True';
      var param = {};

      if (is_temp) {
        param.product_id = product_id;
      } else {
        param.product_temp = product_id;
      }

      if (product_id) {
        this._call("/application/add_product", param).then(function (resposne) {
          if (resposne) {
            if (is_temp) {
              $("#product_temp_details").modal('hide');
              wSaleUtils.animateClone($('#d_orders_qty span'), $(ele).closest('.card'), -50, 10);
              $("#d_orders_qty .my_cart_quantity").html(resposne.cart_quantity).hide().fadeIn(600);
              ele.classList.add('active');
            } else {
              var html = `
                <div class="modal hide fade in" id="product_temp_details">
                  <div class="modal-dialog" role="document">
                    <div class="modal-content">
                      <div class="modal-body">
                      ${resposne}
                      </div>
                    </div>
                  </div>
                </div>
              `;
              $("#product_temp_details").remove();
              $('.dashboard-all-products .product-container').append(html);
              $("#product_temp_details").modal('show');
              $('.modal-backdrop').addClass('model-custom-background');
            }
          }
        });
      }

    },


    getPagerConfig: function (text) {
      var limit = parseInt(this.$target.find('.dashboard-dynamic-pager').find('[name="limit"]').val(), 10);
      var total = parseInt(this.$target.find('.dashboard-dynamic-pager').find('.total').text(), 10);
      text = text.split("-");
      var min = utils.confine(parseInt(text[0], 10), 1, total);
      var max = utils.confine(parseInt(text[1], 10), 1, total);
      if (!isNaN(min)) {
        if (!isNaN(max)) {
          limit = utils.confine((max-min)+1, 1, total);
        } else {
          limit = 1;
        }
        this.param = {
          'limit': limit,
          'offset': min-1
        }
        return true
      }
      return false;
    },

    _setPagerParameter: function (ev) {
      if ( ! ev.currentTarget.classList.contains('disabled') ) {
        var text = this.$target.find('.dashboard-dynamic-pager').find('[type="text"]').val();
        var limit = parseInt(this.$target.find('.dashboard-dynamic-pager').find('[name="limit"]').val(), 10);
        var total = parseInt(this.$target.find('.dashboard-dynamic-pager').find('.total').text(), 10);
        var proceed = this.getPagerConfig(text);

        if (proceed) {

          this.param.limit = limit;
          if ( ev.currentTarget.classList.contains('o_pager_next') ) {
            this.param.offset = utils.confine(this.param.offset + this.param.limit, 0, total);
          } else {
            this.param.offset = utils.confine(this.param.offset - this.param.limit, 0, total);
          }

          var url = this.$target.find('.dashboard-dynamic-pager').find('[name="url"]').val();
          var $ele = this.$target.find(".dashboard-side-nav").find(`[d_href="${url}"]`)
          this._render($ele);
        }

      }
    },

    _setFetchParameter: function (ev) {
      var text = ev.currentTarget.value;
      var proceed = this.getPagerConfig(text);
      if (proceed) {
        var url = this.$target.find('.dashboard-dynamic-pager').find('[name="url"]').val();
        var $ele = this.$target.find(".dashboard-side-nav").find(`[d_href="${url}"]`);
        this._render($ele);
      }
    },

    _setFetchParameterCall: function (ev) {
      var keycode = (event.keyCode ? event.keyCode : event.which);
      if(keycode == '13'){
        this._setFetchParameter(ev);
      }
    },

    _search_items: function(ev) {
      this._trigger_active_view();
    }
  })

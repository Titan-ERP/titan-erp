/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { renderToElement } from "@web/core/utils/render";
import { rpc } from "@web/core/network/rpc";

publicWidget.registry.websiteDealerFAQ = publicWidget.Widget.extend({
  xmlDependencies: ['/dealership_management/static/src/xml/faq_template.xml'],
  selector: '.dealer-faqs',
  events: {
    'click .category button': '_callAllLines',
    'click .read-more': '_callSingleFaq',
    'click #get_back_faq': '_getBackFAQ',
    'click .search-faq-items button': '_searchFAQItems'
  },
  init() {
    this._super(...arguments);
  },

  start: function () {
    var id = parseInt(this.$target.find('#accordion').attr('data_id'), 10);
    this.params = { 'faq_category': id };
    this.back = $("#get_back_faq");
    var self = this;
    self._callBack = function (resposne) {
      self.back.addClass('d-none');
      $('#dealer_faq_item').remove();
      var html = renderToElement('dealership_management.dealer_faq_items', resposne);
      self.$target.find('#accordion').html(html);
      $("#head").text(resposne.head);
      $("#item_description").text(resposne.description);
      $('.category button').removeClass('active');
      $(`.category #${self.params.faq_category}`).addClass('active');
    };
    this._getFAQLines(self._callBack);
  },

  _callAllLines: function (ev) {
    var id = parseInt(ev.currentTarget.getAttribute("id"), 10);
    this.params.faq_category = id;
    this._getFAQLines(this._callBack);
  },

  _callSingleFaq: function (ev) {
    ev.preventDefault();
    var self = this;
    var id = parseInt(ev.currentTarget.getAttribute("id"), 10);
    this.params.faq = id;
    var $ele = this.$target.find('#accordion');
    var _callBack = function functionName(resposne) {
      $ele.find('.card').hide('fast');
      self.back.removeClass('d-none');
      $ele.append(renderToElement('dealership_management.dealer_faq_item', resposne));
    };

    this._getFAQLines(_callBack);
  },

  _getBackFAQ: function (ev) {
    ev.preventDefault();
    var $ele = this.$target.find('#accordion');
    $('#dealer_faq_item').remove();
    $ele.find('.card').show('fast');
    this.back.addClass('d-none');
  },

  _searchFAQItems: function (ev) {
    var search = this.$target.find('.search-faq-items input').val();
    if (search.length > 1) {
      this.params.search = search;
      this._getFAQLines(this._callBack);
    }
  },


  _getFAQLines: function (_callBack) {
    var self = this;
    rpc('/dealer/faq_items', self.params).then(function (resposne) {
      _callBack(resposne);
      self.params = {};
    })
  }

});
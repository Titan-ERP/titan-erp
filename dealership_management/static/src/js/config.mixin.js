/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

  var ConfigMixin = {
    _changeCountry: function (country_id, $state, $hide_ele) {
      var $first = $state.find('option').first();
      $state.find('option').remove().end().append($first);

      if (country_id) {
        var url = `/shop/country_infos/${country_id}`;
        rpc(url).then(function (response) {
          if (response) {
            var state = response.states;
            var $option = ""
            state.forEach(function (item) {
              $option += `<option value=${item[0]}>${item[1]}</option>`;
            })
            if ($option.length > 0) {
              $state.find('option').end().append($option);
              if ($hide_ele) {
                $hide_ele.removeClass('d-none');
              }
            } else {
              if ($hide_ele) {
                $hide_ele.addClass('d-none');
              }
            }
          }
        })
      }
    },

    _createMarkers: function (response) {
      var self = this;
      response.applications.forEach(function (dict) {
        if (! dict.coords) {
          self._addMarkerUsingAddress(dict);
        } else {
          self._addMarker(dict);
        }
      });
    },

    _addMarkerUsingAddress: function (dict) {
      var self = this;
      self.Geocoder.geocode({'address': dict.full_address}, function(results, status) {
        if (status === google.maps.GeocoderStatus.OK) {
          dict.coords = results[0].geometry.location;
          self._addMarker(dict);
        } else {
          console.log("coords not found for address:", status);
        }
      });
    },

    _addMarker: function (config) {}
  }

  export default ConfigMixin;
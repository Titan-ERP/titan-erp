/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import ConfigMixin from "@dealership_management/js/config.mixin";
import { renderToElement } from "@web/core/utils/render";
import { _t } from "@web/core/l10n/translation";
import { markup } from "@odoo/owl";
import { post } from "@web/core/network/http_service";
import { rpc } from "@web/core/network/rpc";
var count = 0;

  publicWidget.registry.websiteDealerLocator = publicWidget.Widget.extend(ConfigMixin,{
    xmlDependencies: [
      '/dealership_management/static/src/xml/locator_template.xml',
      '/portal/static/src/xml/portal_chatter.xml',
      '/portal_rating/static/src/xml/portal_tools.xml',
      '/portal_rating/static/src/xml/portal_chatter.xml'
    ],
    selector: '.dealer-locator',
    events: {
      "change #country_id": "_changeCountryAddress",
      'submit .form-find-location form': '_submit',
      'click .use-current-location p': '_activeGeolocation',
      'click .application .get-direction': 'getDirection',
      'click .applications .pager .btn': '_getPagerSubmit',
      'click .applications .call_dealer': '_getLeadsModel',
      'click .applications .post_review': '_getReviewModel',
      'click .modal .create_leads': '_setLeadsModel',
      'click .modal .pager .btn': '_getModalPagerSubmit',
      'click .modal .rating_value .fa': '_setStar',
      'click .modal .load_more_review': 'loadMoreReview',
      'mouseenter .modal .rating_value .fa': '_setStar',
      'click #create_leads_model .form-submit .btn-primary': '_submitLeads',
      'click #create_review_model .form-submit .btn-primary': '_submitReview',
    },



    init: function(){
      this._super.apply(this, arguments);
      rpc('/check/active_locator').then(async function(response){
            if (response.flag){
              await $.getScript(`//maps.googleapis.com/maps/api/js?key=${response.gmap_api_key}&amp;libraries=places`); 
            }
            else{
              await $.getScript(`//maps.google.com/maps/api/js`);
            }
        });
    },
    

    start: function () {
      this.params= {};
      this.localCoords = {};
      this.model = $("#create_leads_model");
      this.Rmodel = $("#create_review_model");
      this.$dealer = $('.modal .selected-dealer');
      this.$form = this.model.find('form');
      this._super.apply(this, arguments);
    },

    _changeCountryAddress: function (ev) {
      var country_id = ev.currentTarget.value;
      var $state = this.$target.find("#state_id");
      this._changeCountry(country_id, $state, $state.closest('.col-md-6'));
    },

    _setParams: function (ev) {
      if (ev.type == 'submit') {
        var $form = $(ev.currentTarget);
        this.params.plan_id = parseInt($form.find('[name="plan_id"]').find(":selected").val(), 10) || '';
        this.params.country_id = parseInt($form.find('#country_id').find(":selected").val(), 10) || '';
        this.params.state_id = parseInt($form.find('#state_id').find(":selected").val(), 10) || '';
        this.params.city = $form.find('[name="city"]').val();
        this.params.zip = $form.find('[name="zip"]').val();
      } else if (this.params.useLocation === true) {
        delete this.params.useLocation;
      } else {
        var json_data = JSON.parse(ev.currentTarget.parentElement.querySelector('.domain').value);
        json_data['offset'] = this.params.offset;
        this.params = json_data;
      }
    },

    _parseResponse: function (resposne) {
      resposne.applications.forEach(function (dict) {
        var address = dict.street;
        address += dict.city ? `, ${dict.city}` : '';
        address += dict.state_id ? `, ${dict.state_id[1]}` : '';

        if (! dict.coords) {
          var full_address = address;
          full_address += dict.country_id[1] ? `, ${dict.country_id}` : '';
          dict.full_address = full_address.replace(', ', ',');
        }
        dict.content = renderToElement('dealership_management.dealer_location_content', {'app': dict});
        dict.address = address;
      })

      return resposne
    },

    _submit: function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var self = this;

      this._setParams(ev);

      $.post('/dealer/dealer_locator', this.params, function (resposne) {
        self.params= {};
        $('.err_dealer_locator').addClass('d-none');
        if (resposne.applications) {
          self.dealers = resposne.applications;
          resposne = self._parseResponse(resposne);
          for(var i =0;i<resposne.applications.length;i++){
            resposne.applications[i].ratings = markup(resposne.applications[i].ratings)
          }
          var $item = renderToElement('dealership_management.dealer_locations', resposne);
          self.$target.find('.applications').empty().append($item);
          
          var map_ele = document.getElementById('g_map');
          map_ele.parentElement.style.height = "400px";
          self.Geocoder = new google.maps.Geocoder();
          self.service = new google.maps.DistanceMatrixService();


          self.map = new google.maps.Map(map_ele, {
            zoom: 4,
            center: {lat: 0.0, lng: 0.0},
            mapTypeId: google.maps.MapTypeId.ROADMAP
          });

          self._createMarkers(resposne);


          self.directionsService = new google.maps.DirectionsService();
          self.directionsRenderer = new google.maps.DirectionsRenderer();
          self.bounds = new google.maps.LatLngBounds();
          self.directionsRenderer.setMap(self.map);


        } else {
          $('.err_dealer_locator').removeClass('d-none');
        }

      });
    },

    _getPagerSubmit: function (ev) {
      var offset = parseInt(ev.currentTarget.parentElement.querySelector('.offset').value);
      var limit = parseInt(ev.currentTarget.parentElement.querySelector('.limit').value);

      if (ev.currentTarget.classList.contains('o_pager_next')) {
        this.params.offset = offset + limit;
      } else {
        this.params.offset = offset - limit;
      }
      if (! this.params.load) {
        this._submit(ev);
      }
    },

    _activeGeolocation: function (ev) {
      var plan_id = parseInt($('.form-find-location').find('select[name="plan_id"]').find(":selected").val(), 10) || false;
      var self = this;

      $.get('https://ipapi.co/json').then(function (resposne, status) {
        if (status == 'success') {
          if (plan_id != false) {
            self.params.plan_id = plan_id;
          };
          self.params.country_code = resposne.country_code;
          self.params.state_name = resposne.region;
          self.city = resposne.city;
          self.zip = resposne.postal;
          self.params.useLocation = true;
          self._submit(ev);
        }
      });

    },

    _addMarker: function (config) {
      var map = this.map;
      var self = this;
      var $ele = $(`#dealer_coords_${config.id}`);

      var marker = new google.maps.Marker({
        position: config.coords,
        map: this.map,
      });

      var infoWindow = new google.maps.InfoWindow({
        content: config.content
      });

      marker.addListener('click', function () {
        if (self.activeWindow) {
          self.activeWindow.close();
        }
        infoWindow.open(map, marker);
        self.activeWindow = infoWindow;
      });

      $ele.attr('coords', JSON.stringify(config.coords));
      self.bounds.extend(marker.position);
      self.map.fitBounds(self.bounds);
      self.getDistance(config.coords, $ele);
    },

    getDirection: function (ev) {
      ev.preventDefault();
      var self = this;
      var destination = false;
      var address = false
      try {
        destination = JSON.parse($(ev.currentTarget).closest('.application').attr('coords'));
      } catch (e) {
        var dealer = $(ev.currentTarget).closest('.application').attr('id');
        dealer = parseInt(dealer.replace('dealer_coords_', ''));
        var application = self.dealers.filter(function (x) {
          return x.id == dealer
        })
        if (application.length > 0) {
          destination = application[0]['address'];
          address = true;
        }
      }
      if (Object.keys(destination).length > 0) {
        this.getCurrentPosition().then(function (resposne) {
          var origin = new google.maps.LatLng(resposne.location.lat, resposne.location.lng);
          destination = address ? destination : new google.maps.LatLng(destination.lat, destination.lng);
          var request = {
            origin: origin,
            destination: destination,
            travelMode: 'DRIVING',
            unitSystem: google.maps.UnitSystem.IMPERIAL
          };

          self.directionsService.route(request, function(response, status) {
            if (status == 'OK') {
              self.directionsRenderer.setDirections(response);
            } else {
              window.open(`http://maps.google.com/maps?saddr=${request.origin}&daddr=${request.destination}`);
            }
          });
        })
      }
    },

    getDistance: function (coords, $ele) {
      var self = this;
      self.getCurrentPosition().then(function(response) {
        self._callForDistance(coords, $ele);
      });
    },

    _callForDistance: function (coords, $ele) {
      var results = {};
      results.origins = [this.localCoords];
      results.destinations = [coords];
      results.travelMode = 'DRIVING',

      this.service.getDistanceMatrix(results, function (origins, status) {
        if (status == 'OK') {
          if (origins.rows) {
            var results = origins.rows[0].elements;
            for (var j = 0; j < results.length; j++) {
              var element = results[j];
              if (element.status == 'OK') {
                var info = `<span><span class="rm mr-2"><span class="fa fa-road" /> ${element.distance.text}</span>`;
                info += `<span class="rm"><span class="fa fa-clock-o" /> ${element.duration.text}</span></span><br/>`;
                var $address = $ele.find('address');
                if ($address.find('.fa-road').length <= 0) {
                  $address.append(info);
                }
              }
            }
          }
        }
      });
    },

    _setDealer: function (ev) {
      var html = $(ev.currentTarget).closest('.application').clone();
      html.find('.rm').remove();
      html.find('.rm-before').remove();
      html.addClass('alert alert-success');
      html.find('.rm-before').remove();
      this.$dealer.html(html);
    },

    _getLeadsModel: function (ev) {
      ev.preventDefault();
      this.Rmodel.modal('hide');
      var $dealers = $('.dealers-items');
      var applications = $(ev.currentTarget).closest('.applications').clone();
      applications.find('.application').addClass('clone').find('.rm-before').remove();
      this._setDealer(ev);
      $dealers.html(applications.html());
      this.model.modal('show');
      $(".modal-backdrop").addClass("model-custom-background");
    },

    _getReview: function (thread_id, count) {
      var params = {
        thread_model: "dealership.application",
        thread_id: thread_id,
        count: count
      };
      var resposne = new Promise(function (resolve, error) {
        rpc('/dealer/review/messages',params).then(function (response) {
          var $target = $('.dealers-reviews');
          $target.html(response.review_data);
          if (response.is_load_more) {
            $('.load_more_review').removeClass('d-none');
          }
          else {
            $('.load_more_review').addClass('d-none');
          }
          resolve(true);
        });
      });
      return resposne;
    },

    loadMoreReview: function () {
      count = count + 1;
      this._getReview(this._dealer, count);
    },

    _getReviewModel: function (ev) {
      this.model.modal('hide');
      var self = this;
      self.review_offset = 0;
      self._dealer = parseInt(ev.currentTarget.getAttribute('thread_id'));
      this._setDealer(ev);

      self.Rmodel.find('[name="thread_id"]').val(self._dealer);
      self.Rmodel.modal('show');
      this._getReview(self._dealer, 0).then(function (response) {
        $(".modal-backdrop").addClass("model-custom-background");
      });
    },

    _setLeadsModel: function (ev) {
      ev.preventDefault();
      var $ele = $(ev.currentTarget).closest('.application');
      if ($ele.hasClass('clone')) {
        var html = $ele.clone();
        html.find('.rm').remove();
        html.addClass('alert alert-success');
        html.find('.rm-before').remove();
        this.$dealer.html(html);
      }
    },

    _getModalPagerSubmit: function (ev) {
      var self = this;
      this.params.load = true;
      this._getPagerSubmit(ev);
      this._setParams(ev);
      $.post('/dealer/dealer_locator', this.params, function (resposne) {
        if (resposne.applications) {
          resposne = self._parseResponse(resposne);
          self.params= {};
          var $dealers = $('.dealers-items');
          var $item = renderToElement('dealership_management.dealer_locations', resposne);
          $item.find('.application').addClass('clone');
          $item.find('.rm-before').remove();
          $dealers.html($item);
        }
      });
    },

    _submitLeads: function (ev) {
      $(ev.currentTarget).addClass('disabled');
      var $input = this.$form.find('input, textarea');
      var proceed = true;
      var params = {};
      var self = this;

      $input.each(function () {
        $(this).popover('dispose')
        if (! this.checkValidity()) {
          $(this).popover({content: `<span class="text-danger"> ${this.validationMessage}</span>`, html: true});
          $(this).popover('show');
          proceed = false;
          $(ev.currentTarget).removeClass('disabled');
          return proceed;
        } else {
          var value = this.value;
          params[this.getAttribute('name')] = value;
        }
      });

      if (proceed) {
        post('/website/form/crm.lead', params).then(function (response) {
          self.model.find('input[type="text"], textarea, input[type="email"],input[type="checkbox"]').val('');
          self.model.modal('hide');
          $(ev.currentTarget).removeClass('disabled');
        });
      }
    },

    _setStar: function (ev) {
      var current_star = $(ev.currentTarget);
      var value = parseInt(current_star.attr('value')) * 1;
      current_star.parents('.th_dealer_rating ').find('.fa').removeClass('fa-star').addClass('fa-star-o');
      current_star.prevAll().removeClass('fa-star-o').addClass('fa-star');
      current_star.removeClass('fa-star-o').addClass('fa-star');
      this.Rmodel.find('[name="rating_value"]').val(value);
    },

    _submitReview: function (ev) {
      ev.preventDefault();
      var self = this;
      var $form = $(ev.currentTarget).parent().parent();
      var star_value = $form.find('[name="rating_value"]').val();
      var msg_value = $form.find('[name="body"]').val();
      var danger = $form.find('.alert-danger');
      danger.addClass('d-none');
      if (star_value.length <= 0 || !msg_value) {
        danger.removeClass('d-none');
      } else {
        var params = { post_data: {} };
        $form.serializeArray().forEach(function (ele) {
          if (ele.name == 'thread_model') {
            params[ele.name] = ele.value;
          }
          else if (ele.name == 'thread_id') {
            params[ele.name] = parseInt(ele.value);
          }
          else {
            params.post_data[ele.name] = ele.value;
          }
        });
        params.post_data['message_type'] = "comment";
        params.post_data['subtype_xmlid'] = "mail.mt_comment";
        rpc('/mail/message/post', params).then(function (response) {
          if(response.temp) {
            var button = $(`[thread_id="${params.thread_id}"]`);
            var $application = button.closest('.application');
            $application.find('.th_dealer_rating').html($(response.temp).html());
            button.find('span').html(response.count);
            self.Rmodel.modal('hide');
            $('.items-devide').show();
          }
        });
      }
    },

    getCurrentPosition: function () {
      var self = this;
      return new Promise(function (resolve, reject) {
        if ( Object.keys(self.localCoords).length === 0 ) {
          var call = $.get('https://location.services.mozilla.com/v1/geolocate?key=test');
          if (navigator.geolocation) {
            function success(pos) {
              var coords = {
                "location": {
                  "lat": pos.coords.latitude,
                  "lng": pos.coords.longitude
                }
              }
              self.localCoords = coords;
              resolve(self.localCoords);
            };
            function error(err) {
              call.then(function (data) {
                self.localCoords.location = data.location;
                resolve(self.localCoords);
              })
            };
            navigator.geolocation.getCurrentPosition(success, error);
          } else {
            call.then(function (data) {
              self.localCoords.location = data.location;
              resolve(self.localCoords);
            })
          }
        } else {
          resolve(self.localCoords);
        }
      });
    }

  });
  export default publicWidget.registry.websiteDealerLocator;
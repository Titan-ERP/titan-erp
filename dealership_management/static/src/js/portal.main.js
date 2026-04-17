/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import publicWidget from "@web/legacy/js/public/public_widget";
import ConfigMixin from "@dealership_management/js/config.mixin";

  publicWidget.registry.websiteDealerShipForm = publicWidget.Widget.extend(ConfigMixin,{
    selector: ".dealership_mangement_signup_form",
    events: {
      'click .form-user-container .nav-item': '_tabClick',
      "change #country_id": "_changeCountryAddress",
      "change #interested_country_id": "_changeCountryIntrested",
      "change [name='has_xp']": "_changeExperience",
      "change [name='has_code']": "_addCode",
      "click .proceed": "_proceedForm",
      "submit form": "_submitForm",
      "click .add-experience": "_addExperienceRow",
      "click .action .edit": "_editExperienceRow",
      "click .action .delete": "_deleteExperienceRow",
    },

    init() {
      this._super(...arguments);
    },

    start: function () {
      var $btn = this.$target.find('form .tab-pane').last().find('.proceed')
      $btn.attr('type', 'submit');
      $btn.removeClass('proceed');
      $btn.html(_t('Submit'));
    },

    _changeCountryAddress: function (ev) {
      var country_id = ev.currentTarget.value;
      var $state = this.$target.find("#state_id");
      this._changeCountry(country_id, $state, $state.parents('.form-group'));
    },

    _changeCountryIntrested: function (ev) {
      var country_id = ev.currentTarget.value;
      var $state = this.$target.find("#interested_state_id");
      this._changeCountry(country_id, $state, $state.parents('.form-group'));
    },

    _tabClick: function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.currentTarget.classList.contains("reslved")) {
        $(ev.currentTarget).tab('show');
      }
    },

    _proceedForm: function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var $input = $(ev.currentTarget.parentElement).find('input[required], select[required]');
      var $inputEmail = $(ev.currentTarget.parentElement).find('input[type="email"]');
      var reg = /^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$/;
      var proceed = true;

      $input.each(function () {
        if ($(this).val().length <= 0) {
          $(this).addClass("error");
          if ($(this).closest('.form-group').find('.error-message').length <= 0) {
            $(this).after(`<p class="h5 mt-1 error-message text-danger mb-0">${_t("Required field can not be empty")}</p>`);
          }
          this.parentElement.scrollIntoView();
          proceed = false;
        } else {
          $(this).removeClass("error");
          $(this).closest('.form-group').find('.error-message').remove();
        }
      });

      $inputEmail.each(function () {
        if ((reg.test($(this).val()) == false)){
            $(this).addClass("error");
            if ($(this).closest('.form-group').find('.error-message').length <= 0) {
              $(this).after(`<p class="h5 mt-1 error-message text-danger mb-0">${_t("Invalid email address.")}</p>`);
            }
            this.parentElement.scrollIntoView();
            proceed = false;
        } else {
          $inputEmail.removeClass("error");
          $(this).closest('.form-group').find('.error-message').remove();
        }
      });

      if (proceed) {
        var id = $(ev.currentTarget).closest(".tab-pane").attr("id");
        var $currentNav = this.$target.find(`#${id}-tab`);
        $currentNav.addClass("reslved");
        $currentNav.next().addClass("reslved");
        $currentNav.next().tab('show');
      }

    },

    _changeExperience: function (ev) {
      var $parent = $(ev.currentTarget).closest('.d-flex.one');
      var $currentExp = $parent.find('[name="current_business"]')
      var $ele = this.$target.find('[has_xp="true"]');

      if (ev.currentTarget.checked == true) {
        this.has_xp = true;
        $currentExp.addClass('dealer-none');
        $ele.removeClass('dealer-none');
      } else {
        this.has_xp = false;
        $ele.addClass('dealer-none');
        $currentExp.removeClass('dealer-none');
      }
    },

    _submitForm: function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var $currentNav = ev.currentTarget.parentElement;
      var $form = this.$target.find("form");
      var url = $form.attr("action");
      var params = {};
      var removeField = ["business_name", "business_from", "business_to", "first_name", "last_name"];
      var fullName = {};
      var $tr = this.$target.find(".added-experience tbody tr");
      var experience = [];

      $form.serializeArray().forEach(function (data) {
        if (removeField.indexOf(data.name) < 0 && data.value != "") {
          params[data.name] = data.value;
        }
        if (data.name == "first_name") {
          fullName.fName = data.value;
        }
        if (data.name == "last_name") {
          fullName.lName = data.value;
        }
      });

      if (this.has_xp) {
        $tr.each(function (index) {
          var data = {
              "name": $(this).find(".name").text(),
              "from_date": $(this).find(".from").text(),
              "to_date": $(this).find(".to").text()
          }
          experience.push(data);
        })

        if (experience.length >= 0) {
          params["business_xp_ids"] = experience;
        }
      }

      params["name"] = fullName.lName.length <= 0 ? fullName.fName : fullName.fName + " " + fullName.lName;
      rpc(url,params).then(function (response) {
        var $error = $(".dealership_application_error");
        if (response.result) {
          $error.addClass('d-none');
          $(ev.currentTarget).find('button[type="submit"]').closest(".tab-pane").html(response.template);
        } else if (response.template){
          $error.empty();
          var html = `<div class="alert alert-danger mb-2 mt-2" >`;

          response.template.forEach(function functionName(error) {
            html += `<p class="mb-0 h5">${error.name}: ${error.type}</p>`;
          });

          html += "</div>";
          $error.append(html);
          $error.removeClass('d-none');
        }
      })
    },

    setFields: function (action, val) {
      var $name = this.$target.find("[name='business_name']");
      var $from = this.$target.find("[name='business_from']");
      var $to = this.$target.find("[name='business_to']");

      if (action == "get") {
        return {
          "name": $name.val(),
          "from": $from.val(),
          "to": $to.val()
        }
      } else{

        if (val) {
          $name.val(val.name);
          $from.val(val.from);
          $to.val(val.to);
        } else {
          $name.val("");
          $from.val("");
          $to.val("");
        }

      }
    },

    _addExperienceRow: function (ev) {
      ev.preventDefault()
      ev.stopPropagation();
      var val = this.setFields("get");
      var $table = this.$target.find(".added-experience");

      if (val.name.length > 0) {
        var html = `
          <tr>
            <td><p class="mb-0 name">${val.name}</p></td>
            <td><p class="mb-0 from">${val.from}</p></td>
            <td><p class="mb-0 to">${val.to}</p></td>
            <td class="action">
              <a href="#" class="edit"><span class="fa fa-pencil" /></a>
              <a href="#" class="delete ml-2"><span class="fa fa-trash-o" /></a>
            </td>
          </tr>
        `;
        $table.find("tbody").append(html);
        $table.removeClass("d-none");
        this.setFields("set", false);
      }
    },

    _deleteExperienceRow: function (ev) {
      ev.preventDefault();
      var table = this.$target.find(".added-experience");
      $(ev.currentTarget.closest("tr")).remove();
      if (table.find("tbody tr").length <= 0){
        table.addClass("d-none");
      }
    },

    _editExperienceRow: function (ev) {
      ev.preventDefault();
      var $tr = $(ev.currentTarget.closest("tr"));
      var vals = {
        "name": $tr.find(".name").text(),
        "from": $tr.find(".from").text(),
        "to": $tr.find(".to").text()
      }
      this.setFields("set", vals);
      $tr.find(".delete").trigger('click');
    },

    _addCode: function (ev) {
      var $hasCode = $(".has_code");
      if (ev.currentTarget.checked == true) {
        $hasCode.removeClass('d-none');
      } else {
        $hasCode.addClass('d-none');
      }
    }

  });

  publicWidget.registry.websiteDealerShipStatus = publicWidget.Widget.extend({
    selector: ".dealership_mangement_status",
    events: {
      "change .file-extention input": "_addAttachment",
      "change [name='forgot_id']": "_changeAccess",
      "submit .information form": "_uploadAttachment",
      'click .get_contract': '_getApplicationContract'
    },

    init() {
      this._super(...arguments);
    },

    _changeAccess: function (ev) {
      var $request_id = $('[name="request_id"]');
      var $check_status = $(".check_status.btn");
      var $get_request = $(".get_security.btn");

      if (ev.currentTarget.checked == true) {
        $request_id.removeAttr("required").closest('.form-group').addClass("d-none");
        $check_status.addClass('d-none');
        $get_request.removeClass('d-none');
      } else {
        $request_id.attr("required", "required").closest('.form-group').removeClass("d-none");
        $check_status.removeClass('d-none');
        $get_request.addClass('d-none');
      }
    },

    _addAttachment: function (ev) {
      var self = this;
      var $parent = $(ev.currentTarget).closest('.file-extention');
      var file = ev.currentTarget.files[0];
      var reader = new FileReader();

      reader.onload = function(e) {
        $(ev.currentTarget).attr("data-url", e.target.result);
        $(ev.currentTarget).attr("data-name", file.name);
        $parent.find("span").remove();
        $parent.find("strong").append(
          ` <span class='text-dark'> [${file.name.length > 30 ? file.name.substr(0,27)+"..." : file.name}]</span>`
        );
      };

      reader.readAsDataURL(file);
    },

    _uploadAttachment: function (ev) {
      ev.preventDefault();
      var $form = $(ev.currentTarget);
      var $file = $form.find('[type="file"]');
      var text = $form.find('[name="response"]').val();

      if (text.length > 0) {
        var $loader = $("#dealership_loader");
        $loader.removeClass("d-none");

        var param = {
          "response": text,
          "history": parseInt($form.attr("id"))
        }

        if ($file.length > 0) {
          param.datas = $file.attr("data-url");
          param.name = $file.attr("data-name");
        }

        rpc('/dealer/add_history_attachment',param).then(function (response) {
          if (response.result) {
            var $container = $form.closest('.d-flex');
            $container.find(".btn-primary").find('span').text("Information Added");
            var html = `<div class="control">
              <p class="col-form-label text-dark"><strong>${response.query}</strong></p>
              <p class="col-form-label mb-2 text-dark">${response.ans}</p>`;

            if (response.data_url) {
              html += `<a class="btn-color" target="new" href="${response.data_url}">
                <span class="fa fa-file"	/>  ${_t("See Attachment")}
              </a>`
            }
            html += "</div>";
            $form.closest(".information").html(html);
          } else {
            $form.find(".alert").remove();
            $form.append(`<p class='alert alert-danger mt-4 mb-0'>${response.error}</p>`);
          }
          $loader.addClass("d-none");
        },function (error) {
          $form.find(".alert").remove();
          $form.append(`<p class='alert alert-danger mt-4 mb-0'>${_("All Information has mandatory")}</p>`);
          $loader.addClass("d-none");
        });

      }
    },

    _getApplicationContract: function (ev) {
      var $container = $(ev.currentTarget.parentElement);
      var _plan = $container.find('[name="plan"]').val();
      var _app = $container.find('[name="application"]').val();

      rpc('/dealer/get_contract',{
          "application": parseInt(_app),
          "plan": parseInt(_plan)
      }).then(function (response) {
        $("#application_contracts").remove();
        $container.closest(".portal_plans").append(response);
        $("#application_contracts").modal('show');
        $(".modal-backdrop").addClass("model-custom-background");
      })

    }

  });
  export default publicWidget.registry.websiteDealerShipForm;
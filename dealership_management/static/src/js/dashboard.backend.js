/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { Component, onWillStart, onMounted, useRef } from "@odoo/owl";
var IsLoadedJS = true;

var data = {
    labels: ['Red', 'Blue', 'Yellow', 'Green', 'Purple', 'Orange'],
    datasets: [{
        label: '# of Votes',
        data: [12, 19, 3, 5, 2, 3],
        backgroundColor: '#5C9BEF',
        borderColor: '#5C9BEF',
        borderWidth: 1
    }]
};

var options = {
    legend: {
        display: false
    },
    plugins: {
        emptyDoughnut: {
            color: '#5C9BEF',
            width: 2,
            radiusDecrease: 20
        }
    }
};

var scales = {
    x: {
        gridLines: {
            drawOnChartArea: false,
        },
        ticks: {
            beginAtZero: true
        }
    },
    y: {
        gridLines: {
            drawOnChartArea: false,
        },
        ticks: {
            beginAtZero: true
        }
    }
};


var text = _t('REGISTRATION')
var centerText = {
    center: {
        text: text,
    }
};

class DealershipDashboard extends Component {
    setup() {
        this.topproducts = useRef("topProducts");
        this.topleads = useRef("topleads");
        this.ctxregistration = useRef("ctxregistration");
        this.planfilter = useRef("planfilter");
        this.contractstate = useRef("contractstate");
        this.salesstate = useRef("salesstate");
        this.state = useRef("state");
        this.gmap = useRef("gmap");
        this.action = useService("action");
        onWillStart(async () => {
            await this._loadDashBoard()
        });

        onMounted(() => {
            this.chartTopLeads();
            this.chartTopProducts();
            this.chartDealerRegistration();
            this.chartPlanFilter();
            this.chartContractState();
            this.chartSaleState();
            this._initDealer();
        });
    }

    _loadDashBoard() {
        var self = this;
        return rpc('/dashboard/home').then(function (response) {
            self.dataset = response;
        })
    };

    _loadData(params) {
        return rpc('/dashboard/update_data', params)
    };

    chartTopLeads() {
        var max = Math.max(this.dataset.total_leads_stat.datasets[0].data) + 5;
        var $ele = $(this.topleads.el);
        var l_option = {
            indexAxis: 'y',
            // Elements options apply to all of the options unless overridden in a dataset
            // In this case, we are setting the border of each horizontal bar to be 2px wide
            elements: {
                bar: {
                    borderWidth: 1,
                }
            },
            responsive: true,
            plugins: {
                legend: {
                    position: 'top',
                },
                title: {
                    display: true,
                    text: 'LEADS STATUS'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    stacked: true,
                    grid: {
                        offset: true
                    }
                }
            }
        };

        var myChart = new Chart($ele, {
            type: 'bar',
            data: this.dataset.total_leads_stat,
            options: l_option
        });
    };

    chartTopProducts() {
        var $ele = $(this.topproducts.el);
        this.ctx_top_products = this._renderChart($ele, 'bar', this.dataset.top_products_stat, options);
    }

    chartDealerRegistration() {
        var $ele = $(this.ctxregistration.el);
        this.ctx_registration = this._renderChart($ele, 'doughnut', this.dataset.total_registration_stat, options);
    }

    chartPlanFilter() {
        var $ele = $(this.planfilter.el);
        this.ctx_plan_filter = this._renderChart($ele, 'doughnut', this.dataset.plan_stat, options);
    }

    chartContractState() {
        var $ele = $(this.contractstate.el);
        this.ctx_contract_state = this._renderChart($ele, 'doughnut', this.dataset.contract_state, options);
    }

    chartSaleState() {
        var $ele = $(this.salesstate.el);
        this.ctx_sales_state = this._renderChart($ele, 'doughnut', this.dataset.total_sale_stat, options);
    }

    _chartRegistry() {

        const plugin = {
            id: 'emptyDoughnut',
            afterDraw: function (chart) {
                let totalData = chart.config.data.datasets[0].data.reduce(function (
                    a,
                    b
                ) {
                    return a + b;
                },
                    0);
                if (totalData === 0) {
                    const {
                        chartArea: { left, top, right, bottom },
                        ctx
                    } = chart;
                    ctx.save(); // Save the current canvas state

                    // Calculate the center of the chart
                    const centerX = (left + right) / 2;
                    const centerY = (top + bottom) / 2;

                    // Calculate the radii of the inner and outer circles of the doughnut
                    const outerRadius = Math.min(right - left, bottom - top) / 2;
                    const innerRadius = outerRadius - 45; // Adjust this value as needed

                    // Calculate the positions for the starting and ending points of the line
                    const lineStartX = centerX;
                    const lineStartY = centerY - outerRadius;
                    const lineEndX = centerX;
                    const lineEndY = centerY - innerRadius;

                    // Draw the outer arc (grey doughnut ring)
                    ctx.beginPath();
                    ctx.arc(centerX, centerY, outerRadius, 0, 2 * Math.PI);
                    ctx.fillStyle = '#5C9BEF';
                    ctx.fill();

                    // Draw the inner arc (to clear the inner circle)
                    ctx.beginPath();
                    ctx.arc(centerX, centerY, innerRadius, 0, 2 * Math.PI);
                    ctx.fillStyle = 'rgba(255, 255, 255, 1)'; // Fill with white to clear the inner circle
                    ctx.fill();

                    // Draw the white line break from outer circle to inner circle
                    ctx.beginPath();
                    ctx.moveTo(lineStartX, lineStartY);
                    ctx.lineTo(lineEndX, lineEndY);
                    ctx.lineWidth = 2; // Adjust the line width as needed
                    ctx.strokeStyle = 'rgba(255, 255, 255, 1)'; // White color
                    ctx.stroke();

                    ctx.restore(); // Restore the canvas state
                }
            },
        };
        return plugin;
    }

    _changeChartType(ev) {
        try {
            var dataset = ev.currentTarget.dataset;
            var selector = dataset.value;
            var type = dataset.type;
            var data = this.dataset[dataset.load];
            var $ele = $(ev.currentTarget).parents('.card-body').find(`#${selector}`);
            var $button = $ele.closest('.card').find('.dropdown-toggle');

            if ($button.length > 0) {
                $button.attr('data-chart', type);
            }

            this[selector].destroy();
            var new_chart = this._renderChart($ele, type, data, options);
            this[selector] = new_chart;
        } catch (e) {
            console.log(e);
        }
    }

    _changeChartData(ev) {
        var self = this;
        var $el = $(ev.currentTarget);
        var selector = ev.currentTarget.dataset.value;
        var type = ev.currentTarget.dataset.type;
        var $ele = $(ev.currentTarget).parents('.card-body').find(`#${selector}`);
        var $card = $el.closest('.card');

        $el.closest('.btn-group').find('button').removeClass('active');
        $el.addClass('active');
        $card.find('canvas').addClass('d-none');
        $ele.removeClass('d-none');

        var call = $el.data('call');
        $card.find('.dropdown-toggle').each(function () {
            this.dataset.call = call;
            this.dataset.selector = selector
        })

        if (self.hasOwnProperty(selector)) {
            self[selector].destroy();
        }
        this._getUpdatedChartData($card.find('.dropdown-toggle').first())
    }

    _updateChartData(ev) {
        ev.preventDefault();
        var value = ev.currentTarget.dataset.value;
        var text = ev.currentTarget.innerHTML;
        var ele = $(ev.currentTarget).parent().prev('button');
        var $button = $(ev.currentTarget).closest('.btn-group').find('.dropdown-toggle');
        $button.html(text);
        $button.attr('data-value', value);
        $button.trigger('change');
        this._getUpdatedChartData(ele)
    }

    _getUpdatedChartData(ele) {
        var self = this;

        var ele = ele;
        var $card = $(ele).closest('.card').find('.card-header');
        var selector = ele[0].dataset.selector;
        var type = ele[0].dataset.chart;
        var params = { 'call': ele[0].dataset.call };
        var $ele = ele.parents('.card-header').next().find(`#${selector}`);

        $card.find('.dropdown-toggle').each(function () {
            params[this.dataset.type] = parseInt(this.dataset.value);
        })

        self._loadData(params).then(function (response) {
            if (response) {
                if (self[selector]) {
                    self[selector].destroy();
                }
                var new_chart = self._renderChart($ele, type, response, options);
                self[selector] = new_chart;
                self.dataset[params.call] = response;
            }
        });
    }

    colorLighter(color, percent) {
        var num = parseInt(color.replace('#', ''), 16),
            amt = Math.round(2.55 * percent),
            R = (num >> 16) + amt,
            B = (num >> 8 & 0x00FF) + amt,
            G = (num & 0x0000FF) + amt;
        var color = (0x1000000 + (R < 255 ? R < 1 ? 0 : R : 255) * 0x10000 + (B < 255 ? B < 1 ? 0 : B : 255) * 0x100 + (G < 255 ? G < 1 ? 0 : G : 255)).toString(16).slice(1);
        return "#" + color;
    }

    _renderChart(ctx, type, data, option) {
        var x_option = {};
        Object.assign(x_option, option);

        if (type != 'doughnut') {
            x_option['scales'] = scales;
        } else {
            try {
                var length = data.datasets[0].data.length;
                var color = data.datasets[0].backgroundColor;
                if (typeof color == 'string') {

                    var update_color = [color];
                    for (let i = 0; i < length; i++) {
                        color = this.colorLighter(color, 5);
                        update_color.push(color);
                    }
                    data.datasets[0].backgroundColor = update_color;
                    data.datasets[0].hoverBackgroundColor = update_color;
                    data.datasets[0].borderColor = update_color;
                    data.datasets[0].hoverBorderColor = update_color;
                }
            } catch (e) { }

            if (ctx.attr('id') == 'ctx_registration') {
                x_option.elements = centerText;
                x_option['cutoutPercentage'] = 75;
                x_option['aspectRatio'] = 2;
            }
            if (ctx.attr('id') == 'ctx_sales_state') {
                x_option['aspectRatio'] = 2;
            }
        }

        x_option.legend.display = false;
        if (data.legend_text) {
            ctx.parent().find('.legend').remove();
            var html = "<div class='w-100 legend row mt-4'>";
            data.legend_text.forEach(function (item, index) {
                var color = data.datasets[0].backgroundColor;
                if (typeof color != 'string') {
                    color = color[index];
                }
                html += `<div class="col-6 mt-2 mb-2 text-dark"><strong><i class="fa fa-circle mr-2" style="color: ${color}"/>${item}</strong></div>`;
            })
            html += "</div>";
            ctx.after(html);
        }
        var myChart = new Chart(ctx, {
            type: type,
            data: data,
            options: x_option,
            plugins: [this._chartRegistry()],
        });

        return myChart;
    }


    _initDealer() {
        var self = this;
        if (this.dataset.location.map_key.length > 0 && IsLoadedJS) {
            var api = `https://maps.googleapis.com/maps/api/js?key=${this.dataset.location.map_key}&amp;libraries=places`;
            $.getScript(api, function () {
                self.dataset.location.map_api = true;
                IsLoadedJS = false;
                self._getDealer();
            });
        } else if (!typeof google === 'undefined') {
            self.dataset.location.map_api = true;
            self._getDealer();
        }
    }



    _getDealer() {
        var self = this;
        var map_ele = $(this.gmap.el);
        var $card = $(map_ele).closest(".card-body");
        $card.find('.alert').addClass('d-none');

        if (this.dataset.location.map_api) {
            var params = {
                'call': 'dealer_location_stat'
            }


            if (this.dataset.location.current_state) {
                params.state_id = parseInt(this.dataset.location.current_state);
            }
            if (this.dataset.location.current_country) {
                params.country_id = parseInt(this.dataset.location.current_country);
            }

            self._loadData(params).then(function (response) {
                if (response.applications.length) {
                    map_ele.height = "400px";

                    self.map = new google.maps.Map(map_ele, {
                        zoom: 4,
                        center: { lat: 0.0, lng: 0.0 },
                        mapTypeId: google.maps.MapTypeId.ROADMAP
                    });

                    self.Geocoder = new google.maps.Geocoder();
                    self.bounds = new google.maps.LatLngBounds();

                    response = self._parseResponse(response);
                    self._createMarkers(response);
                } else {
                    $card.find('.not-found').removeClass('d-none');
                }
            });
        } else {
            $card.find('.api_missing').removeClass('d-none');
        }

    }

    _updateMapData(ev) {
        ev.preventDefault();
        var self = this;
        var ele = ev.currentTarget;
        var value = parseInt(ele.dataset.value);
        var el = ele.innerHTML;
        var $state = $(this.state.el);
        $(ele).closest('.btn-group').find('.dropdown-toggle').html(el);

        if (ele.classList.contains('country')) {
            this.dataset.location.current_country = value;
            this.dataset.location.current_state = false;
            $state.html(_t('State'));
            var url = `/shop/country_infos/${value}`;
            rpc(url).then(function (response) {
                var dropDown = $state.closest('.btn-group').find('.dropdown-menu');
                dropDown.empty();
                if (response) {
                    var state = response.states;
                    var $option = "";
                    state.forEach(function (item) {
                        $option += `<a class="dropdown-item" data-value="${item[0]}" href="#" >${item[1]}</a>`;
                    })
                    dropDown.html($option);
                }
            })
        } else {
            this.dataset.location.current_state = value;
        }
        self._getDealer();
    }

    _parseResponse(response) {
        var data = [];
        response.applications.forEach(function (item) {
            var dict = {};
            dict.count = String(item[item.length - 1]);
            item.splice(-1, 1);
            dict.full_address = response.name + ' ' + item.join(" ");
            data.push(dict);
        })
        return { 'applications': data };
    }

    _addMarker(config) {
        var self = this;
        var marker = new google.maps.Marker({
            position: config.coords,
            map: this.map,
        });

        var infoWindow = new google.maps.InfoWindow({
            content: 'Dealers ' + config.count
        });

        marker.addListener('click', function () {
            if (self.activeWindow) {
                self.activeWindow.close();
            }
            infoWindow.open(self.map, marker);
            self.activeWindow = infoWindow;
        });

        self.bounds.extend(marker.position);
        self.map.fitBounds(self.bounds);
    }

    _getLeadsPage(ev) {
        ev.preventDefault();
        var domain = ['user_id', '!=', false];

        if (ev.currentTarget.dataset.assigned) {
            domain = ['user_id', '=', false];
        }

        domain = [domain];
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: _t('Leads'),
            res_model: 'crm.lead',
            views: [[false, 'list']],
            view_mode: 'list',
            domain: domain
        });
    }

    _openRecords(ev) {
        var item = $(ev.currentTarget).attr('action');
        var length = this.dataset.top_header_stats.length
        for (let i = 0; i < length; i++) {
            if (this.dataset.top_header_stats[i].id == item) {
                var action = this.dataset.top_header_stats[i]
            }
        }
        this.action.doAction({
            type: 'ir.actions.act_window',
            views: action.views,
            name: action.name,
            res_model: action.model,
            search_view_id: [false],
            domain: action.domain
        });
    }
}

DealershipDashboard.template = "dealership_management.dealer_backend_dashboard";
registry.category("actions").add('dealer_backend_dashboard', DealershipDashboard);
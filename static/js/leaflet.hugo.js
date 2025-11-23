let leafletMapsObj = {};
let leafletMarkersObj = {};

function drawTrack(trackOpts, elevationOpts, markerOpts) {
    const map = leafletMapsObj[trackOpts.mapId];
    if (!map) {
        console.warn("drawTrack: map not found for mapId:", trackOpts.mapId);
        return;
    }

    if (!trackOpts.trackPath) {
        console.error("drawTrack: trackPath is missing for mapId:", trackOpts.mapId);
        return;
    }

    var opts = {
        elevationControl: {
            options: {
                closeBtn: false,
                autofitBounds: trackOpts.fitTrack,
                position: elevationOpts.graphPosition,
                theme: elevationOpts.graphTheme,
                width: elevationOpts.graphWidth,
                height: elevationOpts.graphHeight,
                margins: {
                    top: elevationOpts.margins.top,
                    right: elevationOpts.margins.right,
                    bottom: elevationOpts.margins.bottom,
                    left: elevationOpts.margins.left
                },
                followMarker: elevationOpts.graphFollowMarker,
                collapsed: elevationOpts.graphCollapsed,
                detached: elevationOpts.graphDetached,
                legend: false,
                summary: false,
                downloadLink: '',
                gpxOptions: {
                    async: true,
                    polyline_options: {
                        className: 'track-' + trackOpts.trackId + '-',
                        color: trackOpts.lineColor,
                        opacity: trackOpts.lineOpacity,
                        weight: trackOpts.lineWeight,
                        // Canvas renderer + light smoothing keep large GPX files responsive when zooming
                        renderer: map._trackRenderer || (map._trackRenderer = L.canvas({ padding: 0.5 })),
                        smoothFactor: 1.2,
                    },
                    marker_options: {
                        startIcon: new L.ExtraMarkers.icon({
                            icon: markerOpts.iconStart,
                            markerColor: markerOpts.iconStartColor,
                            shape: markerOpts.iconStartShape,
                            prefix: 'fa',
                            extraClasses: markerOpts.iconStartClasses
                        }),
                        endIcon: new L.ExtraMarkers.icon({
                            icon: markerOpts.iconEnd,
                            markerColor: markerOpts.iconEndColor,
                            shape: markerOpts.iconEndShape,
                            prefix: 'fa',
                            extraClasses: markerOpts.iconEndClasses
                        }),
                        wptIcons: {
                            '': new L.ExtraMarkers.icon({
                                icon: markerOpts.icon,
                                markerColor: markerOpts.iconColor,
                                shape: markerOpts.iconShape,
                                prefix: 'fa',
                                extraClasses:  markerOpts.iconClasses,
                            })
                        }
                    }
                },
            },
        },
    };


    if (!trackOpts.showElevation) {
        // This is a dummy, empty div that I set in the html template
        opts.elevationControl.options.elevationDiv = "#elevation-empty";
    }

    const controlElevation = L.control.elevation(opts.elevationControl.options)
        .addTo(map);

    controlElevation.load(trackOpts.trackPath);
}

window.downloadFile = function (sUrl) {

    //iOS devices do not support downloading. We have to inform user about this.
    if (/(iP)/g.test(navigator.userAgent)) {
        alert('Your device does not support files downloading. Please try again in desktop browser.');
        return false;
    }

    //If in Chrome or Safari - download via virtual link click
    if (window.downloadFile.isChrome || window.downloadFile.isSafari) {
        //Creating new link node.
        var link = document.createElement('a');
        link.href = sUrl;

        if (link.download !== undefined) {
            //Set HTML5 download attribute. This will prevent file from opening if supported.
            var fileName = sUrl.substring(sUrl.lastIndexOf('/') + 1, sUrl.length);
            link.download = fileName;
        }

        //Dispatching click event.
        if (document.createEvent) {
            var e = document.createEvent('MouseEvents');
            e.initEvent('click', true, true);
            link.dispatchEvent(e);
            return true;
        }
    }

    // Force file download (whether supported by server).
    if (sUrl.indexOf('?') === -1) {
        sUrl += '?download';
    }

    window.open(sUrl, '_self');
    return true;
};

window.downloadFile.isChrome = navigator.userAgent.toLowerCase().indexOf('chrome') > -1;
window.downloadFile.isSafari = navigator.userAgent.toLowerCase().indexOf('safari') > -1;

function createMap(mapnode) {
    const mapId = mapnode.getAttribute("mapId");
    const latAttr = parseFloat(mapnode.getAttribute("mapLat"));
    const lonAttr = parseFloat(mapnode.getAttribute("mapLon"));
    // fall back to sensible defaults if attributes are missing/empty
    const mapLat = Number.isFinite(latAttr) ? latAttr : 52.52;
    const mapLon = Number.isFinite(lonAttr) ? lonAttr : 13.4;
    const zoomAttr = parseInt(mapnode.getAttribute("zoom") || mapnode.getAttribute("Zoom"), 10);
    const zoom = Number.isFinite(zoomAttr) ? zoomAttr : 13;
    const scrollWheelZoom = mapnode.getAttribute("scrollWheelZoom") !== "false";

    //Create Map
    leafletMapsObj[mapId] = L.map("mapid_" + mapId, {
        preferCanvas: true,
        scrollWheelZoom: scrollWheelZoom,
        maxZoom: 17, // clamp to tile availability to avoid blank map when over-zooming
        minZoom: 2,
    }).setView([mapLat, mapLon], zoom);

    //Add tiles
    L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
        maxZoom: 17,
        maxNativeZoom: 17,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(leafletMapsObj[mapId]);
};

function createMarker(markernode) {
	markerId=markernode.getAttribute("markerId")
	markerLat=markernode.getAttribute("markerLat")
	markerLon=markernode.getAttribute("markerLon")
	mapId=markernode.getAttribute("mapId")
	//Marker
	leafletMarkersObj[markerId] = L.marker([markerLat, markerLon]).addTo(leafletMapsObj[mapId]);
	/*{{ if $markerContent }}
		leafletMarkersObj[{{ $markerId }}].bindPopup("{{ $markerContent }}").openPopup();
	    {{ end }}*/
};


// Initialise everything for a single map node:
// - create the map
// - create all markers belonging to it
// - draw all tracks belonging to it, but delayed one by one to make it feel faster
function initMapAndChildren(mapNode) {
    var mapId = mapNode.getAttribute("mapId");

    if (!mapId) {
        console.warn("initMapAndChildren: mapId attribute missing on node", mapNode);
        return;
    }

    createMap(mapNode);

    // 2) Create markers associated with this mapId
    var markers = Array.prototype.slice.call(
        document.querySelectorAll('.leaflet-marker[mapId="' + mapId + '"]')
    );
    markers.forEach(function (markerNode) {
        createMarker(markerNode);
    });

    // 3) Draw tracks associated with this mapId, delayed
    if (window._leafletTracks && Array.isArray(window._leafletTracks)) {
        var tracks = window._leafletTracks.filter(function (entry) {
            return entry.trackOpts && entry.trackOpts.mapId === mapId;
        });

        // Stagger loading each track to avoid one huge CPU spike
        tracks.forEach(function (entry, index) {
            // You can tune the delay; for very heavy tracks increase this value.
            var delay = index * 150;
            setTimeout(function () {
                drawTrack(entry.trackOpts, entry.elevationOpts, entry.markerOpts);
            }, delay);
        });
    }
}

// Lazy-load maps when they come into view
window.addEventListener('load', function () {
    var maps = Array.prototype.slice.call(document.getElementsByClassName("leaflet-map"));

    if ('IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function (entries, obs) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) {
                    return;
                }

                var mapNode = entry.target;
                initMapAndChildren(mapNode);

                // Stop observing once initialised
                obs.unobserve(mapNode);
            });
        }, { rootMargin: '200px' }); // start a bit before visible

        maps.forEach(function (node) {
            observer.observe(node);
        });
    } else {
        // Fallback for older browsers: initialise everything immediately
        maps.forEach(function (node) {
            initMapAndChildren(node);
        });
    }
});

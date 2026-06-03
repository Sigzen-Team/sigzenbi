/**
 * Superset Dashboard Embedding Utility
 */

const SupersetManager = {
    /**
     * Initialize the embedding process
     * @param {string} containerId - The ID of the HTML element to mount the dashboard
     * @param {string} dashboardId - The UUID of the dashboard to render
     */
    init: function(containerId, dashboardId) {
        if (!dashboardId) {
            console.error("dashboardId is required to initialize embedding");
            return;
        }
        this.loadSDK(() => {
            this.embed(containerId, dashboardId);
        });
    },

    /**
     * Load the Superset Embedded SDK script dynamically
     */
    loadSDK: function(callback) {
        if (window.supersetEmbeddedSdk) {
            callback();
            return;
        }

        const script = document.createElement("script");
        script.src = "https://unpkg.com/@superset-ui/embedded-sdk";
        script.onload = callback;
        script.onerror = () => console.error("Failed to load Superset SDK");
        document.head.appendChild(script);
    },

    /**
     * Fetch guest token and embed the dashboard
     * @param {string} containerId - The ID of the HTML element to mount the dashboard
     * @param {string} dashboardId - The UUID of the dashboard to render
     */
    embed: function(containerId, dashboardId) {
        const mountPoint = document.getElementById(containerId);

        if (!mountPoint) {
            console.error(`Container #${containerId} not found`);
            return;
        }

        // We call the Client App API which acts as a bridge to the Central App
        const apiUrl = `/api/method/sigzenbi_client.API.dashboard_api.get_superset_token`;
        
        fetch(`${apiUrl}?dashboard_id=${dashboardId}`)
            .then(response => response.json())
            .then(data => {
                const message = data.message;
                
                if (!message || !message.success) {
                    console.error("Token fetch failed:", message ? message.message : "Unknown error");
                    return;
                }

                const guestToken = message.guest_token || message.token;
                const supersetUrl = message.superset_url;

                supersetEmbeddedSdk.embedDashboard({
                    id: dashboardId,
                    supersetDomain: supersetUrl,
                    mountPoint: mountPoint,
                    fetchGuestToken: () => Promise.resolve(guestToken),
                    dashboardUiConfig: {
                        hideTitle: true,
                        filters: {
                            expanded: false
                        }
                    }
                }).then(() => {
                    this.styleIframe(mountPoint);
                });
            })
            .catch(err => {
                console.error("Error fetching guest token:", err);
            });
    },

    /**
     * Apply styles to the generated iframe
     */
    styleIframe: function(container) {
        const iframe = container.querySelector("iframe");
        if (iframe) {
            iframe.style.width = "100%";
            iframe.style.height = "800px";
            iframe.style.border = "none";
            iframe.style.borderRadius = "8px";
            iframe.style.boxShadow = "0 4px 12px rgba(0,0,0,0.1)";
        }
    }
};

// Usage Example:
// SupersetManager.init("dashboard-container", "your-dashboard-uuid-here");

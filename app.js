// JavaScript for dashboard interactivity
// Example: Real-time speed tracking updates
const updateSpeedDisplay = () => {
    // Fetch latest metrics from Supabase
    fetch('/api/speed-metrics')
        .then(response => response.json())
        .then(data => {
            document.getElementById('upload-speed').textContent = data.latestUploadSpeed + ' KB/s';
            document.getElementById('download-speed').textContent = data.latestDownloadSpeed + ' KB/s';
        });
};

// Initialize updates every 5 seconds
setInterval(updateSpeedDisplay, 5000);

// Example: File status toggle
const toggleFileStatus = (fileId) => {
    fetch(`/api/file-status/${fileId}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            document.getElementById(`file-${fileId}`).classList.toggle('active', data.isActive);
        });
};
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'de.astrocortex.app',
  appName: 'Astro Cortex',
  // Web-Root: das von FastAPI ausgelieferte PWA-Verzeichnis
  webDir: '../frontend',
  server: {
    // Kein host eingetragen: die App laedt die gebundelten Assets und
    // erreicht das Backend ueber die im Config-Menue gesetzte BASE-URL
    // (localStorage astro_base, z. B. die Tailscale-Serve-HTTPS-URL).
    androidScheme: 'https',
  },
};

export default config;

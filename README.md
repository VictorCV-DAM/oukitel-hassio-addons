<div align="center">
  <img src="oukitel-bridge/logo.png" width="380" alt="Oukitel Power Station Add-on" />
  <h1>Oukitel Home Assistant Add-ons ⚡</h1>

  <p>Collection of ready-to-run Home Assistant Add-ons for Oukitel Power Stations</p>

  [![GitHub Release](https://img.shields.io/github/v/release/VictorCV-DAM/oukitel-hassio-addons?style=for-the-badge&color=blue)](https://github.com/VictorCV-DAM/oukitel-hassio-addons/releases)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
  [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-orange?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/VictorCV)
  [![PayPal](https://img.shields.io/badge/PayPal-Donate-blue?style=for-the-badge&logo=paypal)](https://paypal.me/victorcava)
  [![Ko-fi](https://img.shields.io/badge/Ko--fi-Donate-red?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/victorcv)
</div>

---

## 🚀 Add Repository to Home Assistant

Click the button below to add this repository directly to your Home Assistant Add-on Store:

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FVictorCV-DAM%2Foukitel-hassio-addons)

Or manually:
1. In Home Assistant, navigate to **Settings** -> **Add-ons** -> **Add-on Store**.
2. Click the three dots (top right) -> **Repositories**.
3. Add: `https://github.com/VictorCV-DAM/oukitel-hassio-addons` and click **Add**.

---

## 📦 Available Add-ons

> [!TIP]
> _All-in-one_ addons are configured to work completely stand-alone inside Home Assistant OS / Supervised.

### ⚡ Oukitel Cloud MQTT Bridge
_All-in-one_

Standalone background service that connects directly to the Wonderfree / Acceleronix Cloud API and publishes all telemetry, diagnostics, and bidirectional control switches (AC, DC 12V, USB) into Home Assistant via MQTT Discovery.

- **No Smartphone / No ADB Needed**: Fully autonomous daemon.
- **MQTT Auto-Discovery**: Automatic creation of sensors and controls in Home Assistant.
- **Dynamic Keep-Alive**: Prevents power station deep-sleep timeouts.
- **Multi-Region**: Supports Europe (`EU`), North America (`US`), and China (`CN`).

[👉 View Add-on Documentation](oukitel-bridge/DOCS.md)

<br/>

<div align="center">
  <table width="100%">
    <tr>
      <td width="50%" align="center">
        <b>Multi-Region Cloud Setup</b><br/><br/>
        <img src="docs/images/02_login_dialog.png" alt="Setup Dialog" width="95%"/>
      </td>
      <td width="50%" align="center">
        <b>Automatic Discovery</b><br/><br/>
        <img src="docs/images/03_device_discovered.png" alt="Device Discovered" width="95%"/>
      </td>
    </tr>
    <tr>
      <td width="50%" align="center">
        <b>Real-Time Telemetry & Controls</b><br/><br/>
        <img src="docs/images/05_device_dashboard.png" alt="Oukitel Dashboard" width="95%"/>
      </td>
      <td width="50%" align="center">
        <b>Adjustable Update Frequency</b><br/><br/>
        <img src="docs/images/04_options_frequency.png" alt="Update Frequency" width="95%"/>
      </td>
    </tr>
  </table>
</div>

---

## 👨‍💻 Author & Intellectual Property

- **Author & Maintainer:** Víctor C. V. ([@VictorCV-DAM](https://github.com/VictorCV-DAM))
- **Email:** `victorcvtrabajo@gmail.com`
- **Copyright:** © 2024-2026 Víctor C. V. All rights reserved.
- **License:** Released under the [MIT License](LICENSE).

---

## ☕ Support & Donations

If this add-on has helped you integrate your power station effortlessly into Home Assistant OS, consider supporting continuous development and maintenance:

- ☕ **Buy Me a Coffee:** [buymeacoffee.com/VictorCV](https://www.buymeacoffee.com/VictorCV)
- 🅿️ **PayPal:** [paypal.me/victorcava](https://www.paypal.me/victorcava)
- 🔴 **Ko-fi:** [ko-fi.com/victorcv](https://ko-fi.com/victorcv)
- 💖 **GitHub Sponsors:** [github.com/sponsors/VictorCV-DAM](https://github.com/sponsors/VictorCV-DAM)

Thank you for your support! ⭐

---

## ⚖️ Disclaimer

This project is an independent community development and is not affiliated with, sponsored by, or endorsed by Oukitel or Quectel/Acceleronix. All product names, logos, and brands are property of their respective owners.

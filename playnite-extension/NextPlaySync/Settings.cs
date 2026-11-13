// Ajustes de NextPlay Sync: la dirección de NextPlay y el código personal (los dos salen en Editar perfil › Cuentas
// vinculadas › Playnite) y lo que pasó la última vez. SettingsViewModel es lo que Playnite enseña y guarda (ISettings).
using System;
using System.Collections.Generic;
using Playnite.SDK;
using Playnite.SDK.Data;

namespace NextPlaySync
{
    public class SyncSettings : ObservableObject
    {
        private string serverUrl = "http://localhost:8000";
        private string token = "";
        private DateTime? lastLibrarySync;
        private string lastResult = "Aún no se ha sincronizado.";

        // La dirección de NextPlay, sin la ruta de la API.
        public string ServerUrl { get => serverUrl; set => SetValue(ref serverUrl, value); }
        // El código personal; vacío, no se manda nada.
        public string Token { get => token; set => SetValue(ref token, value); }
        // Cuándo se mandó la biblioteca entera por última vez (las partidas no cuentan).
        public DateTime? LastLibrarySync { get => lastLibrarySync; set => SetValue(ref lastLibrarySync, value); }
        // Qué pasó la última vez que se mandó algo, para enseñarlo en los ajustes.
        public string LastResult { get => lastResult; set => SetValue(ref lastResult, value); }
    }

    public class SettingsViewModel : ObservableObject, ISettings
    {
        private readonly NextPlaySync plugin;
        private SyncSettings editingClone;
        private SyncSettings settings;

        public SyncSettings Settings
        {
            get => settings;
            set
            {
                settings = value;
                OnPropertyChanged();
            }
        }

        public SettingsViewModel(NextPlaySync plugin)
        {
            this.plugin = plugin;
            Settings = plugin.LoadPluginSettings<SyncSettings>() ?? new SyncSettings();
        }

        // Al abrir los ajustes: una copia, por si se cancelan.
        public void BeginEdit()
        {
            editingClone = Serialization.GetClone(Settings);
        }

        // Al cancelar: lo que había.
        public void CancelEdit()
        {
            Settings = editingClone;
        }

        // Al guardar.
        public void EndEdit()
        {
            plugin.SavePluginSettings(Settings);
        }

        // La dirección tiene que empezar por http:// o https://; el código puede quedarse vacío, y entonces no se manda
        // nada.
        public bool VerifySettings(out List<string> errors)
        {
            errors = new List<string>();
            var url = (Settings.ServerUrl ?? "").Trim();
            if (!url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                && !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                errors.Add("La dirección de NextPlay tiene que empezar por http:// o https://.");
            return errors.Count == 0;
        }
    }
}

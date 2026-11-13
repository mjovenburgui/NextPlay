// Pantalla de ajustes de NextPlay Sync en Playnite, hecha en código en vez de con XAML para que la extensión sea un
// solo archivo .dll: la dirección de NextPlay, el código personal y qué pasó la última vez. Playnite le pone como
// DataContext el SettingsViewModel.
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;

namespace NextPlaySync
{
    public class SettingsView : UserControl
    {
        public SettingsView()
        {
            var panel = new StackPanel { Margin = new Thickness(20) };
            panel.Children.Add(Label("Dirección de NextPlay (sale en Editar perfil › Cuentas vinculadas › Playnite)"));
            panel.Children.Add(Field("Settings.ServerUrl"));
            panel.Children.Add(Label("Tu código personal (en el mismo sitio, con «Generar el código»)"));
            panel.Children.Add(Field("Settings.Token"));
            panel.Children.Add(Label(
                "Se sincroniza al abrir Playnite, una vez al día y cada vez que cierras un juego. "
                + "También a mano, desde el menú principal › NextPlay › Sincronizar ahora."));
            var last = new TextBlock { Margin = new Thickness(0, 12, 0, 0), TextWrapping = TextWrapping.Wrap, Opacity = 0.8 };
            last.SetBinding(TextBlock.TextProperty, new Binding("Settings.LastResult"));
            panel.Children.Add(last);
            Content = panel;
        }

        // Un texto encima de un campo.
        private static TextBlock Label(string text) =>
            new TextBlock { Text = text, Margin = new Thickness(0, 12, 0, 4), TextWrapping = TextWrapping.Wrap };

        // Un campo de texto enlazado a la propiedad path de los ajustes, que se guarda según se escribe.
        private static TextBox Field(string path)
        {
            var box = new TextBox();
            box.SetBinding(TextBox.TextProperty, new Binding(path) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged });
            return box;
        }
    }
}

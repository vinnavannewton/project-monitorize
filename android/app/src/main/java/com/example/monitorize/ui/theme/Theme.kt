package com.example.monitorize.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val MonitorizeDarkScheme = darkColorScheme(
    primary = BreezeAccent,
    onPrimary = Color.White,
    secondary = BreezeButton,
    onSecondary = Color.White,
    tertiary = BreezeSurfaceAlt,
    onTertiary = BreezeText,
    background = BreezeBackground,
    onBackground = BreezeText,
    surface = BreezeSurface,
    onSurface = BreezeText,
    surfaceVariant = BreezeSurfaceAlt,
    onSurfaceVariant = BreezeTextMuted,
    outline = BreezeBorder
)

@Composable
fun MonitorizeTheme(
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            dynamicDarkColorScheme(LocalContext.current)
        }
        else -> MonitorizeDarkScheme
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}

package app.monitorize.android

import app.monitorize.android.discovery.DiscoveredDevice
import org.junit.Assert.assertEquals
import org.junit.Test

class RecentDevicesTest {

    @Test
    fun recentDevicesStayWifiOnlyNewestFirstAndCapped() {
        fun device(id: Int) = DiscoveredDevice("PC $id", "192.168.1.$id", 7110)

        var recent = emptyList<DiscoveredDevice>()
        (1..5).forEach { recent = updatedRecentDevices(recent, device(it)) }
        recent = updatedRecentDevices(recent, device(3).copy(name = "Updated PC"))
        recent = updatedRecentDevices(recent, device(6))
        val unchanged = updatedRecentDevices(
            recent,
            DiscoveredDevice("Local PC (USB)", "127.0.0.1", 7110, isUsb = true)
        )

        assertEquals(
            listOf("192.168.1.6", "192.168.1.3", "192.168.1.5", "192.168.1.4", "192.168.1.2"),
            unchanged.map { it.ip }
        )
        assertEquals("Updated PC", unchanged[1].name)
        assertEquals(
            listOf("192.168.1.6", "192.168.1.5", "192.168.1.4", "192.168.1.2"),
            removedRecentDevices(unchanged, device(3)).map { it.ip }
        )
    }

    @Test
    fun monitorsOnTheSameHostRemainSeparateByPort() {
        val first = DiscoveredDevice("PC — First Virtual Monitor", "192.168.1.2", 7110)
        val second = DiscoveredDevice("PC — Second Virtual Monitor", "192.168.1.2", 7114)

        val recent = updatedRecentDevices(updatedRecentDevices(emptyList(), first), second)

        assertEquals(listOf(7114, 7110), recent.map { it.port })
        assertEquals(listOf(second), removedRecentDevices(recent, first))
    }
}

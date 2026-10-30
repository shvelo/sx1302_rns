/*
 * ABI probe for the libloragw HAL structures used through ctypes.
 *
 * This program is compiled by scripts/abi_check.py against the same headers
 * used to build libloragw.so. It prints the size of each mirrored structure
 * and the offset of every field so the Python definitions can be compared
 * with the C compiler's layout.
 */

#include <stddef.h>
#include <stdio.h>

#include "loragw_hal.h"

#define PRINT_SIZE(type) printf("SIZE %s %zu\n", #type, sizeof(struct type))
#define PRINT_OFFSET(type, field) \
    printf("OFFSET %s %s %zu\n", #type, #field, offsetof(struct type, field))

int main(void)
{
    PRINT_SIZE(lgw_rssi_tcomp_s);
    PRINT_OFFSET(lgw_rssi_tcomp_s, coeff_a);
    PRINT_OFFSET(lgw_rssi_tcomp_s, coeff_b);
    PRINT_OFFSET(lgw_rssi_tcomp_s, coeff_c);
    PRINT_OFFSET(lgw_rssi_tcomp_s, coeff_d);
    PRINT_OFFSET(lgw_rssi_tcomp_s, coeff_e);

    PRINT_SIZE(lgw_conf_board_s);
    PRINT_OFFSET(lgw_conf_board_s, lorawan_public);
    PRINT_OFFSET(lgw_conf_board_s, clksrc);
    PRINT_OFFSET(lgw_conf_board_s, full_duplex);
    PRINT_OFFSET(lgw_conf_board_s, com_type);
    PRINT_OFFSET(lgw_conf_board_s, com_path);

    PRINT_SIZE(lgw_conf_rxrf_s);
    PRINT_OFFSET(lgw_conf_rxrf_s, enable);
    PRINT_OFFSET(lgw_conf_rxrf_s, freq_hz);
    PRINT_OFFSET(lgw_conf_rxrf_s, rssi_offset);
    PRINT_OFFSET(lgw_conf_rxrf_s, rssi_tcomp);
    PRINT_OFFSET(lgw_conf_rxrf_s, type);
    PRINT_OFFSET(lgw_conf_rxrf_s, tx_enable);
    PRINT_OFFSET(lgw_conf_rxrf_s, single_input_mode);

    PRINT_SIZE(lgw_conf_rxif_s);
    PRINT_OFFSET(lgw_conf_rxif_s, enable);
    PRINT_OFFSET(lgw_conf_rxif_s, rf_chain);
    PRINT_OFFSET(lgw_conf_rxif_s, freq_hz);
    PRINT_OFFSET(lgw_conf_rxif_s, bandwidth);
    PRINT_OFFSET(lgw_conf_rxif_s, datarate);
    PRINT_OFFSET(lgw_conf_rxif_s, sync_word_size);
    PRINT_OFFSET(lgw_conf_rxif_s, sync_word);
    PRINT_OFFSET(lgw_conf_rxif_s, implicit_hdr);
    PRINT_OFFSET(lgw_conf_rxif_s, implicit_payload_length);
    PRINT_OFFSET(lgw_conf_rxif_s, implicit_crc_en);
    PRINT_OFFSET(lgw_conf_rxif_s, implicit_coderate);

    PRINT_SIZE(lgw_conf_demod_s);
    PRINT_OFFSET(lgw_conf_demod_s, multisf_datarate);

    PRINT_SIZE(lgw_tx_gain_s);
    PRINT_OFFSET(lgw_tx_gain_s, rf_power);
    PRINT_OFFSET(lgw_tx_gain_s, dig_gain);
    PRINT_OFFSET(lgw_tx_gain_s, pa_gain);
    PRINT_OFFSET(lgw_tx_gain_s, dac_gain);
    PRINT_OFFSET(lgw_tx_gain_s, mix_gain);
    PRINT_OFFSET(lgw_tx_gain_s, offset_i);
    PRINT_OFFSET(lgw_tx_gain_s, offset_q);
    PRINT_OFFSET(lgw_tx_gain_s, pwr_idx);

    PRINT_SIZE(lgw_tx_gain_lut_s);
    PRINT_OFFSET(lgw_tx_gain_lut_s, lut);
    PRINT_OFFSET(lgw_tx_gain_lut_s, size);

    PRINT_SIZE(lgw_pkt_rx_s);
    PRINT_OFFSET(lgw_pkt_rx_s, freq_hz);
    PRINT_OFFSET(lgw_pkt_rx_s, freq_offset);
    PRINT_OFFSET(lgw_pkt_rx_s, if_chain);
    PRINT_OFFSET(lgw_pkt_rx_s, status);
    PRINT_OFFSET(lgw_pkt_rx_s, count_us);
    PRINT_OFFSET(lgw_pkt_rx_s, rf_chain);
    PRINT_OFFSET(lgw_pkt_rx_s, modem_id);
    PRINT_OFFSET(lgw_pkt_rx_s, modulation);
    PRINT_OFFSET(lgw_pkt_rx_s, bandwidth);
    PRINT_OFFSET(lgw_pkt_rx_s, datarate);
    PRINT_OFFSET(lgw_pkt_rx_s, coderate);
    PRINT_OFFSET(lgw_pkt_rx_s, rssic);
    PRINT_OFFSET(lgw_pkt_rx_s, rssis);
    PRINT_OFFSET(lgw_pkt_rx_s, snr);
    PRINT_OFFSET(lgw_pkt_rx_s, snr_min);
    PRINT_OFFSET(lgw_pkt_rx_s, snr_max);
    PRINT_OFFSET(lgw_pkt_rx_s, crc);
    PRINT_OFFSET(lgw_pkt_rx_s, size);
    PRINT_OFFSET(lgw_pkt_rx_s, payload);
    PRINT_OFFSET(lgw_pkt_rx_s, ftime_received);
    PRINT_OFFSET(lgw_pkt_rx_s, ftime);

    PRINT_SIZE(lgw_pkt_tx_s);
    PRINT_OFFSET(lgw_pkt_tx_s, freq_hz);
    PRINT_OFFSET(lgw_pkt_tx_s, tx_mode);
    PRINT_OFFSET(lgw_pkt_tx_s, count_us);
    PRINT_OFFSET(lgw_pkt_tx_s, rf_chain);
    PRINT_OFFSET(lgw_pkt_tx_s, rf_power);
    PRINT_OFFSET(lgw_pkt_tx_s, modulation);
    PRINT_OFFSET(lgw_pkt_tx_s, freq_offset);
    PRINT_OFFSET(lgw_pkt_tx_s, bandwidth);
    PRINT_OFFSET(lgw_pkt_tx_s, datarate);
    PRINT_OFFSET(lgw_pkt_tx_s, coderate);
    PRINT_OFFSET(lgw_pkt_tx_s, invert_pol);
    PRINT_OFFSET(lgw_pkt_tx_s, f_dev);
    PRINT_OFFSET(lgw_pkt_tx_s, preamble);
    PRINT_OFFSET(lgw_pkt_tx_s, no_crc);
    PRINT_OFFSET(lgw_pkt_tx_s, no_header);
    PRINT_OFFSET(lgw_pkt_tx_s, size);
    PRINT_OFFSET(lgw_pkt_tx_s, payload);

    return 0;
}

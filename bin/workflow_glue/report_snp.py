#!/usr/bin/env python
"""Create SNV report."""

import os

from aplanat.parsers.bcfstats import parse_bcftools_stats_multi
from dominate.tags import p
from ezcharts.components.ezchart import EZChart
from ezcharts.components.reports.labs import LabsReport
from ezcharts.components.theme import LAB_head_resources
from ezcharts.layout.snippets import DataTable, Stats, Tabs
from ezcharts.plots import util
from ezcharts.plots.categorical import barplot
from ezcharts.plots.matrix import heatmap
import numpy as np
import pandas as pd

from .util import get_named_logger, wf_parser  # noqa: ABS101

# Global variables
Colors = util.Colors

# Mutation profile palette to match the COSMIC
# patterns.
cmap = {
    'C>A': Colors.cerulean,
    'C>G': Colors.black,
    'C>T': Colors.cinnabar,
    'T>A': Colors.grey70,
    'T>C': Colors.medium_spring_bud,
    'T>G': Colors.fandango,
}


def indel_sizes(df):
    """Extract indel sizes with added 0-values."""
    df['nlength'] = df['length (deletions negative)'].astype(int)
    df['count'] = df['number of sites'].astype(int)
    # pad just to pull out axes by a minimum
    counts = df.groupby('nlength') \
        .agg(count=pd.NamedAgg(column='count', aggfunc='sum')) \
        .reset_index()
    # Create min/max range of values
    all_counts = pd.DataFrame({
        'nlength': np.arange(counts.nlength.min() - 1, counts.nlength.max() + 1)
    })
    all_counts = pd.merge(all_counts, counts, on="nlength", how="left").fillna(0)
    # Add values where needed:
    return all_counts


def parse_changes(bcftools_dt):
    """Parse changes from counts using same method as in aplanat."""
    df = bcftools_dt
    sim_sub = {
        'G>A': 'C>T', 'G>C': 'C>G', 'G>T': 'C>A',
        'T>A': 'A>T', 'T>C': 'A>G', 'T>G': 'A>C'}

    def canon_sub(sub):
        b1 = sub[0]
        if b1 not in {'A', 'C'}:
            return canon_sub(sim_sub[sub])
        else:
            return b1, sub[2]

    df['canon_sub'] = df['type'].apply(canon_sub)
    df['Reference allele'] = df['canon_sub'].apply(lambda x: x[0])
    df['Alternative allele'] = df['canon_sub'].apply(lambda x: x[1])
    df['count'] = df['count'].astype(int)
    df = df[['Reference allele', 'Alternative allele', 'count']] \
        .groupby(['Reference allele', 'Alternative allele']) \
        .agg(count=pd.NamedAgg(column='count', aggfunc='sum')) \
        .reset_index()
    df2 = df.pivot(
        index="Alternative allele",
        columns="Reference allele",
        values="count")
    return df2


def main(args):
    """Run the entry point."""
    logger = get_named_logger("report_snp")
    # Check that the input files exist
    if not os.path.exists(args.vcf_stats):
        raise FileNotFoundError(f"File {args.vcf_stats} not found.")

    # Load the data
    try:
        bcfstats = parse_bcftools_stats_multi(
            [args.vcf_stats],
            sample_names=[args.sample_name])
    except IndexError:
        bcfstats = {'SN': pd.DataFrame(), 'TSTV': pd.DataFrame()}
    # Instantiate the report
    report = LabsReport(
        "Small variation statistics", "wf-human-variation",
        args.params, args.versions,
        head_resources=[*LAB_head_resources])

    # VCF At-a-glance report

    with report.add_section('At a glance', 'Summary'):
        tabs = Tabs()
        with tabs.add_tab(args.sample_name):
            if bcfstats['SN'].empty:
                p('The bcftools stats file is empty.')
            else:
                bcfstats['SN'].columns = [
                    i.replace('SNP', 'SNV').replace('MNP', 'MNV')
                    for i in bcfstats['SN'].columns
                ]
                titv = bcfstats['TSTV']['ts/tv'].values[0]
                nsites = bcfstats['SN']['records'].values[0]
                nsnvs = bcfstats['SN']['SNVs'].values[0]
                nindels = bcfstats['SN']['indels'].values[0]
                Stats(
                    columns=4,
                    items=[(f'{"{:,}".format(int(nsites))}', 'Variants'),
                           (f'{"{:,}".format(int(nsnvs))}', 'SNVs'),
                           (f'{"{:,}".format(int(nindels))}', 'Indels'),
                           (f'{titv}', 'Ti/Tv')])

    # Base statistics
    with report.add_section('Statistics', 'Stats'):
        tabs = Tabs()
        with tabs.add_tab(args.sample_name):
            if bcfstats['SN'].empty:
                p('The bcftools stats file is empty.')
            else:
                DataTable.from_pandas(
                    bcfstats['SN'].drop(columns=['sample', 'samples']),
                    use_index=False)
                DataTable.from_pandas(
                    bcfstats['TSTV'].drop(columns='sample'),
                    use_index=False)

    # Change type
    with report.add_section('Substitution types', 'Types'):
        p('Base substitutions aggregated across all samples (symmetrised by pairing).')
        tabs = Tabs()
        with tabs.add_tab(args.sample_name):
            if bcfstats['ST'].empty:
                p('The bcftools stats file is empty.')
            else:
                subtype = parse_changes(bcfstats['ST'])
                plt = heatmap(subtype)
                EZChart(plt, 'epi2melabs')

    # Plot mutation spectra if provided
    with report.add_section('Indels length', 'Indels'):
        p('Insertion and deletion lengths aggregated across all samples.')
        tabs = Tabs()
        with tabs.add_tab(args.sample_name):
            if bcfstats['ST'].empty:
                p('The bcftools stats file is empty.')
            else:
                sizes = indel_sizes(bcfstats['IDD'])
                plt = barplot(data=sizes, x="nlength", y="count")
                EZChart(plt, 'epi2melabs')

    # write report
    report.write(args.report)
    logger.info(f"Written report to '{args.report}'.")


def argparser():
    """Create argument parser."""
    parser = wf_parser("report")

    parser.add_argument("report", help="Report output file")
    parser.add_argument(
        "--read_stats", default='unknown',
        help="read statistics output from bamstats")
    parser.add_argument(
        "--vcf_stats", default='unknown',
        help="final vcf stats file")
    parser.add_argument(
        "--sample_name", default='Sample',
        help="final vcf stats file")
    parser.add_argument(
        "--versions", required=True,
        help="directory containing CSVs containing name,version.")
    parser.add_argument(
        "--params", required=True,
        help="directory containing CSVs containing name,version.")

    return parser
